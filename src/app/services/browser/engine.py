import os
import asyncio
import json
import time
import re
import uuid
import weakref
from enum import Enum
from typing import Optional, Dict, Any, List
from collections import OrderedDict
from playwright.async_api import async_playwright, Playwright, BrowserContext, Page, Browser, Error as PlaywrightError
from app.logger import logger
from app.config import CONFIG

from app.services.browser.tab import TabStatus, PersistentTab, ManagedPage

from app.services.browser.session import ProviderSession

class BrowserEngine:
    """
    Singleton manager for the browser process and provider sessions.
    """
    _instance: Optional['BrowserEngine'] = None
    _lock = asyncio.Lock()

    def __init__(self, headless: Optional[bool] = None, is_bootstrap: bool = False):
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.browser_generation = 0
        self.sessions: Dict[str, ProviderSession] = {}
        self.sessions_lock = asyncio.Lock()
        self.management_lock = asyncio.Lock()
        self.user_data_dir = os.path.join(os.getcwd(), ".playwright_data")
        os.makedirs(self.user_data_dir, exist_ok=True)
        self.is_bootstrap = is_bootstrap
        if headless is not None:
            self.headless = headless
        else:
            self.headless = CONFIG["Playwright"].getboolean("headless", False)
        self.max_pages = CONFIG["Playwright"].getint("max_concurrent_pages", 5)
        self.max_total_tabs = CONFIG["Playwright"].getint("max_total_tabs", 50)
        self.is_shutting_down = False
        self._shutdown_started = False
        self._disconnect_handled = False
        
        # Basic provider adapter registry mapping
        from app.services.browser.adapters.gemini_adapter import GeminiProviderAdapter
        self.adapters = {
            "gemini": GeminiProviderAdapter()
        }

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

    async def _ensure_healthy_browser(self):
        if self.is_shutting_down:
            logger.debug("BrowserEngine: Initialization skipped - engine is shutting down.", extra={"generation": self.browser_generation})
            return

        if not self.playwright or not self.browser or not self.browser.is_connected():
            logger.info("BrowserEngine: Initializing Browser...", extra={"generation": self.browser_generation})
            
            if self.browser:
                try: 
                    await self.browser.close()
                except Exception as e:
                    logger.debug(f"BrowserEngine: Best-effort browser close failed: {e}", extra={"generation": self.browser_generation})
            if self.playwright:
                try: 
                    await self.playwright.stop()
                except Exception as e:
                    logger.debug(f"BrowserEngine: Best-effort playwright stop failed: {e}", extra={"generation": self.browser_generation})
            
            try:
                self.playwright = await async_playwright().start()
                self.browser = await self.playwright.chromium.launch(
                    headless=self.headless,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
                )
                
                # Bind disconnect listener for manual closure detection
                self._disconnect_handled = False
                self.browser.on("disconnected", lambda b: self._on_browser_disconnected())
                
                self.browser_generation += 1
                logger.info("BrowserEngine: New generation active.", extra={"generation": self.browser_generation})
            except Exception as e:
                logger.error(f"BrowserEngine: Failed to launch browser: {e}", exc_info=True, extra={"generation": self.browser_generation})
                self.browser = None
                raise

    def _on_browser_disconnected(self):
        """Internal handler for Playwright's disconnected event."""
        if self.is_shutting_down or self._disconnect_handled:
            return
            
        self._disconnect_handled = True
        logger.warning("BrowserEngine: Unexpected browser disconnection detected (Manual closure or crash).", extra={"generation": self.browser_generation})
        # Fire-and-forget terminal shutdown to kill all background loops and prevent recreation
        try:
            asyncio.get_running_loop().create_task(self.close())
        except RuntimeError as e:
            logger.debug("BrowserEngine: Shutdown task scheduling skipped - event loop already closed.", exc_info=True, extra={"generation": self.browser_generation})



    @property
    def active_pages(self) -> int:
        """Counts current active leases (semaphore slots)."""
        return sum(s.active_lease_count for s in self.sessions.values())

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

    async def close(self) -> None:
        try:
            async with self.management_lock:
                if self._shutdown_started: 
                    logger.info("BrowserEngine: Shutdown already in progress or complete.", extra={"generation": self.browser_generation})
                    return
                
                if getattr(self, "is_bootstrap", False):
                    logger.info("BrowserEngine: Shutting down isolated bootstrap engine...", extra={"generation": self.browser_generation})
                else:
                    logger.info("BrowserEngine: Shutting down singleton runtime engine...", extra={"generation": self.browser_generation})
                
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
                
                logger.info(f"BrowserEngine: Closing {len(self.sessions)} provider session(s)...", extra={"generation": self.browser_generation})
                for session in list(self.sessions.values()):
                    try:
                        logger.info(f"BrowserEngine: Closing session resources for {session.name}", extra={"generation": self.browser_generation})
                        await session.close_resources(save_state=True)
                        logger.info(f"BrowserEngine: Session resources for {session.name} closed successfully.", extra={"generation": self.browser_generation})
                    except Exception as e:
                        logger.error(f"BrowserEngine: Exception closing session resources for {session.name}: {e}", exc_info=True)
                        raise
                
                if self.browser:
                    try: 
                        logger.info("BrowserEngine: Closing browser process.", extra={"generation": self.browser_generation})
                        await self.browser.close()
                        logger.info("BrowserEngine: Browser process closed successfully.", extra={"generation": self.browser_generation})
                    except Exception as e:
                        logger.warning(f"BrowserEngine: Error closing browser: {e}", exc_info=True, extra={"generation": self.browser_generation})
                else:
                    logger.info("BrowserEngine: No browser process to close.", extra={"generation": self.browser_generation})
                
                if self.playwright:
                    try: 
                        logger.info("BrowserEngine: Stopping playwright.", extra={"generation": self.browser_generation})
                        await self.playwright.stop()
                        logger.info("BrowserEngine: Playwright instance stopped successfully.", extra={"generation": self.browser_generation})
                    except Exception as e:
                        logger.warning(f"BrowserEngine: Error stopping playwright: {e}", exc_info=True, extra={"generation": self.browser_generation})
                else:
                    logger.info("BrowserEngine: No playwright instance to stop.", extra={"generation": self.browser_generation})
                
                self.sessions.clear()
                self.browser = None
                self.playwright = None
                logger.info("BrowserEngine: Shutdown complete.", extra={"generation": self.browser_generation})
        except Exception as e:
            logger.error(f"BrowserEngine: Shutdown failed midway: {e}", exc_info=True)
            raise

async def get_browser_engine(headless: Optional[bool] = None, is_bootstrap: bool = False) -> BrowserEngine:
    if is_bootstrap:
        return BrowserEngine(headless=headless, is_bootstrap=True)
    return await BrowserEngine.get_instance(headless=headless)
