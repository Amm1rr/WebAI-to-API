import asyncio
import time
from typing import Optional, Dict, Any
from app.logger import logger
from app.config import CONFIG

from app.services.browser.errors import BrowserDisconnectedError
from app.services.browser.tab import TabStatus, PersistentTab, ManagedPage

from app.services.browser.session import ProviderSession
from app.services.browser.runtime import BrowserRuntime, create_browser_runtime

class BrowserEngine:
    """
    Singleton manager for the browser process and provider sessions.
    """
    _instance: Optional['BrowserEngine'] = None
    _lock = asyncio.Lock()
    _SHUTDOWN_SOURCES = {"application", "browser-disconnect", "manual/internal"}

    def __init__(self, headless: Optional[bool] = None, is_bootstrap: bool = False):
        self.browser: Optional[Any] = None
        self.browser_generation = 0
        self.sessions: Dict[str, ProviderSession] = {}
        self.sessions_lock = asyncio.Lock()
        self.management_lock = asyncio.Lock()
        self.is_bootstrap = is_bootstrap
        if headless is not None:
            self.headless = headless
        else:
            self.headless = CONFIG["Playwright"].getboolean("headless", False)
        self.runtime: BrowserRuntime = create_browser_runtime(headless=self.headless)
        self.max_pages = CONFIG["Playwright"].getint("max_concurrent_pages", 5)
        self.max_total_tabs = CONFIG["Playwright"].getint("max_total_tabs", 50)
        self.is_shutting_down = False
        self.shutdown_requested = False
        self.shutdown_source: Optional[str] = None
        self._shutdown_started = False
        self._disconnect_handled = False
        self._disconnect_close_task: Optional[asyncio.Task] = None

    def request_shutdown(self, source: str) -> bool:
        """Record first shutdown intent without starting resource teardown."""
        if source not in self._SHUTDOWN_SOURCES:
            raise ValueError(f"Unsupported browser shutdown source: {source}")
        if self.shutdown_requested:
            return False

        self.shutdown_requested = True
        self.shutdown_source = source
        if source == "application":
            logger.info(
                "BrowserEngine: Application shutdown requested",
                extra={"shutdown_source": source, "generation": self.browser_generation},
            )
        return True

    @classmethod
    async def get_instance(cls, headless: Optional[bool] = None) -> 'BrowserEngine':
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(headless=headless)
        return cls._instance

    async def get_session(self, provider_name: str, enable_persistence: bool = False) -> ProviderSession:
        async with self.sessions_lock:
            if provider_name not in self.sessions:
                self.sessions[provider_name] = ProviderSession(self, provider_name, enable_persistence=enable_persistence)
            elif enable_persistence:
                self.sessions[provider_name].enable_persistence = True
            return self.sessions[provider_name]

    async def get_page(self, provider: str = "gemini", enable_persistence: bool = False) -> ManagedPage:
        session = await self.get_session(provider, enable_persistence=enable_persistence)
        return await session.acquire_lease()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def _close_sessions_before_browser_replacement(self):
        """Detach provider resources before replacing their parent browser."""
        for session in list(self.sessions.values()):
            if not session._has_resources_to_close():
                continue
            logger.info(
                "BrowserEngine: Closing resources for %s before browser replacement",
                session.name,
                extra={"generation": self.browser_generation},
            )
            await session.close_resources(save_state=False)

    def _track_disconnect_close_task(self, task: asyncio.Task) -> None:
        self._disconnect_close_task = task

        def consume_result(done_task: asyncio.Task) -> None:
            if self._disconnect_close_task is done_task:
                self._disconnect_close_task = None
            if done_task.cancelled():
                return
            try:
                error = done_task.exception()
            except Exception as inspection_error:
                logger.error(
                    "BrowserEngine: Failed to inspect disconnect shutdown task: %s",
                    inspection_error,
                    exc_info=True,
                )
                return
            if error is not None:
                logger.error(
                    "BrowserEngine: Disconnect shutdown task failed: %s",
                    error,
                    exc_info=(type(error), error, error.__traceback__),
                    extra={"generation": self.browser_generation},
                )

        task.add_done_callback(consume_result)

    async def _ensure_healthy_browser(self):
        if self.is_shutting_down:
            logger.debug("BrowserEngine: Initialization skipped - engine is shutting down.", extra={"generation": self.browser_generation})
            return

        if not self.browser or not self.runtime.is_browser_connected(self.browser):
            logger.info("BrowserEngine: Initializing Browser...", extra={"generation": self.browser_generation})

            await self._close_sessions_before_browser_replacement()
            
            if self.browser:
                await self.runtime.close_browser(self.browser, "replacement")
            await self.runtime.stop()
            
            try:
                await self.runtime.start()
                self.browser = await self.runtime.launch_browser()
                
                # Bind disconnect listener for manual closure detection
                self._disconnect_handled = False
                self.runtime.bind_disconnect(self.browser, lambda: self._on_browser_disconnected())
                
                self.browser_generation += 1
                logger.info("BrowserEngine: New generation active.", extra={"generation": self.browser_generation})
            except Exception as e:
                logger.error(f"BrowserEngine: Failed to launch browser: {e}", exc_info=True, extra={"generation": self.browser_generation})
                self.browser = None
                raise

    def _on_browser_disconnected(self):
        """Internal handler for Playwright's disconnected event."""
        self._signal_active_requests(
            lambda: BrowserDisconnectedError("Browser transport disconnected during active request")
        )

        if self.is_shutting_down or self._disconnect_handled:
            return

        if self.shutdown_requested:
            self._disconnect_handled = True
            logger.info(
                "BrowserEngine: Browser disconnected during application shutdown",
                extra={
                    "shutdown_source": self.shutdown_source,
                    "generation": self.browser_generation,
                },
            )
            return
            
        self._disconnect_handled = True
        self.request_shutdown("browser-disconnect")
        logger.warning("BrowserEngine: Unexpected browser disconnection detected (Manual closure or crash).", extra={"generation": self.browser_generation})
        # Fire-and-forget terminal shutdown to kill all background loops and prevent recreation
        try:
            task = asyncio.get_running_loop().create_task(self.close())
            if task is not None:
                self._track_disconnect_close_task(task)
        except RuntimeError as e:
            logger.debug("BrowserEngine: Shutdown task scheduling skipped - event loop already closed.", exc_info=True, extra={"generation": self.browser_generation})



    @property
    def active_pages(self) -> int:
        """Counts current active leases (semaphore slots)."""
        return sum(s.active_lease_count for s in self.sessions.values())

    def _signal_active_requests(self, error_factory) -> None:
        for session in tuple(self.sessions.values()):
            session.signal_active_requests(error_factory)

    def _abort_active_requests(self) -> None:
        for session in tuple(self.sessions.values()):
            session.abort_active_requests()

    async def _wait_for_active_requests_to_release(self) -> None:
        while self.active_pages > 0:
            await asyncio.sleep(0)

    @property
    def total_page_count(self) -> int:
        """Counts all live browser pages across all sessions."""
        return sum(s.page_count for s in self.sessions.values())

    async def enforce_soft_cap(self):
        """
        Enforces the global soft-cap on total browser pages.
        Coordinates best-effort eviction across all provider sessions.
        """
        if self.total_page_count <= self.max_total_tabs:
            return

        logger.warning(f"BrowserEngine: Soft-cap pressure detected ({self.total_page_count}/{self.max_total_tabs})")

        candidates = []
        for session in self.sessions.values():
            session_candidates = await session.get_eviction_candidates()
            candidates.extend((session, tab) for tab in session_candidates)
            
        def get_priority(tab: PersistentTab) -> int:
            if tab.status == TabStatus.INVALIDATING: return 1
            if tab.status == TabStatus.IDLE: return 2
            if tab.status == TabStatus.LEASED: return 3
            return 4

        # Sort by priority then by last accessed (LRU)
        candidates.sort(key=lambda item: (get_priority(item[1]), item[1].last_accessed_at))

        needed_evictions = self.total_page_count - self.max_total_tabs
        evicted = 0
        
        for session, tab in candidates:
            if evicted >= needed_evictions:
                break
                
            now = time.monotonic()
            await tab._lock.acquire()
            try:
                # 1. Skip if already dead or gone
                if tab.status == TabStatus.DEAD:
                    continue
                
                # 2. Detailed re-validation under lock
                if tab.status == TabStatus.IDLE:
                    pass # Still IDLE, safe to evict
                elif tab.status == TabStatus.INVALIDATING:
                    pass # Already doomed
                elif tab.status == TabStatus.LEASED:
                    # ONLY evict LEASED if it's actually stale (Source of Truth: session)
                    is_stale = (now - tab.last_heartbeat_at) > session.lease_timeout
                    if not is_stale or tab.lease_token is None:
                        continue
                else:
                    continue # Unknown or incompatible state
                
                # Transition to INVALIDATING under lock to prevent future leases
                tab.status = TabStatus.INVALIDATING
            finally:
                tab._lock.release()
            
            logger.info(f"BrowserEngine: Evicting tab {tab.conversation_id} due to soft-cap pressure.")
            await tab.close()
            
            # Increment ONLY if physical closure succeeded
            if tab.status == TabStatus.DEAD:
                evicted += 1
            else:
                logger.warning(f"BrowserEngine: Eviction failed for {tab.conversation_id} (Status: {tab.status})")

    async def close(self, source: Optional[str] = None, save_state: Optional[bool] = None) -> None:
        try:
            async with self.management_lock:
                if self._shutdown_started: 
                    logger.info("BrowserEngine: Shutdown already in progress or complete.", extra={"generation": self.browser_generation})
                    return

                self.request_shutdown(source or self.shutdown_source or "manual/internal")
                
                if getattr(self, "is_bootstrap", False):
                    logger.info("BrowserEngine: Shutting down isolated bootstrap engine...", extra={"shutdown_source": self.shutdown_source, "generation": self.browser_generation})
                else:
                    logger.info("BrowserEngine: Shutting down singleton runtime engine...", extra={"shutdown_source": self.shutdown_source, "generation": self.browser_generation})
                
                self.is_shutting_down = True
                self._shutdown_started = True
                
                drain_start = time.monotonic()
                drain_timeout = 15.0
                try:
                    while self.active_pages > 0 and (time.monotonic() - drain_start) < drain_timeout:
                        logger.info(f"BrowserEngine: Waiting for {self.active_pages} active pages to drain...", extra={"generation": self.browser_generation})
                        await asyncio.sleep(1.0)
                except Exception as e:
                    logger.error(f"BrowserEngine: Exception during active pages drain: {e}", exc_info=True)
                    raise

                if self.active_pages > 0:
                    logger.warning(
                        "BrowserEngine: Aborting active requests after drain deadline.",
                        extra={"generation": self.browser_generation},
                    )
                    self._abort_active_requests()
                    try:
                        await asyncio.wait_for(
                            self._wait_for_active_requests_to_release(),
                            timeout=1.0,
                        )
                    except asyncio.TimeoutError:
                        logger.warning(
                            "BrowserEngine: Active requests did not release before teardown continued.",
                            extra={"active_pages": self.active_pages, "generation": self.browser_generation},
                        )
                
                logger.info(f"BrowserEngine: Closing {len(self.sessions)} provider session(s)...", extra={"generation": self.browser_generation})
                should_save_state = self.is_bootstrap if save_state is None else save_state
                for session in list(self.sessions.values()):
                    try:
                        logger.info(f"BrowserEngine: Closing session resources for {session.name}", extra={"generation": self.browser_generation})
                        await session.close_resources(save_state=should_save_state)
                        logger.info(f"BrowserEngine: Session resources for {session.name} closed successfully.", extra={"generation": self.browser_generation})
                    except Exception as e:
                        logger.error(f"BrowserEngine: Exception closing session resources for {session.name}: {e}", exc_info=True)
                        raise
                
                if self.browser:
                    logger.info("BrowserEngine: Closing browser process.", extra={"generation": self.browser_generation})
                    await self.runtime.close_browser(self.browser, "terminal")
                else:
                    logger.info("BrowserEngine: No browser process to close.", extra={"generation": self.browser_generation})
                
                await self.runtime.stop()
                
                self.sessions.clear()
                self.browser = None
                logger.info("BrowserEngine: Shutdown complete.", extra={"generation": self.browser_generation})
        except Exception as e:
            logger.error(f"BrowserEngine: Shutdown failed midway: {e}", exc_info=True)
            raise

async def get_browser_engine(headless: Optional[bool] = None, is_bootstrap: bool = False) -> BrowserEngine:
    if is_bootstrap:
        return BrowserEngine(headless=headless, is_bootstrap=True)
    return await BrowserEngine.get_instance(headless=headless)


def get_existing_browser_engine() -> Optional[BrowserEngine]:
    """
    Non-initializing access to the BrowserEngine singleton.
    Returns the current instance or None; never creates a browser process.
    """
    return BrowserEngine._instance


def request_application_shutdown() -> bool:
    """Mark existing runtime engine shutdown intent from Uvicorn's signal hook."""
    if BrowserEngine._instance is None:
        return False
    return BrowserEngine._instance.request_shutdown("application")
