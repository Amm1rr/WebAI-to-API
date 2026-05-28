import os
import asyncio
import json
import time
import re
from typing import Optional, Dict, Any
from playwright.async_api import async_playwright, Playwright, BrowserContext, Page, Browser, Error as PlaywrightError
from app.logger import logger
from app.config import CONFIG

class ManagedPage:
    """
    A wrapper around a Playwright Page that ensures centralized semaphore 
    release and deterministic resource cleanup.
    """
    def __init__(self, page: Page, session: 'ProviderSession'):
        self.page = page
        self.session = session
        self._released = False
        self._lock = asyncio.Lock()

    async def close(self):
        """Safely closes the page and releases the session semaphore exactly once."""
        async with self._lock:
            if self._released:
                return
            self._released = True
            
            try:
                if not self.page.is_closed():
                    await self.page.close()
            except Exception as e:
                logger.warning(f"ManagedPage: Error closing page: {e}")
            finally:
                self.session.semaphore.release()

class ProviderSession:
    """
    Manages a dedicated BrowserContext for a specific provider (e.g., Gemini, ChatGPT).
    Encapsulates semaphore control, state persistence, and keepalive management.
    """
    def __init__(self, engine: 'BrowserEngine', name: str):
        self.engine = engine
        self.name = name
        self.context: Optional[BrowserContext] = None
        self.keepalive_page: Optional[Page] = None
        self.last_browser_generation = -1
        
        # Concurrency & Lifecycle
        self.semaphore = asyncio.Semaphore(engine.max_pages)
        self.init_lock = asyncio.Lock()   # Serializes setup/health checks
        self.state_lock = asyncio.Lock()  # Serializes disk I/O
        self.autosave_task: Optional[asyncio.Task] = None
        
        # Persistent state
        self.state_path = os.path.join(engine.user_data_dir, f"{name}_state.json")

    @property
    def is_alive(self) -> bool:
        """Checks if the context is initialized, healthy, and belongs to the current browser generation."""
        return (
            self.context is not None and 
            self.engine.browser is not None and 
            self.engine.browser.is_connected() and
            self.last_browser_generation == self.engine.browser_generation
        )

    @property
    def active_pages(self) -> int:
        """Count of active request tabs (excludes keepalive)."""
        if self.context:
            try:
                return max(0, len(self.context.pages) - 1)
            except Exception:
                pass
        return 0

    async def get_page(self) -> ManagedPage:
        """
        Acquires a semaphore permit and returns a new ManagedPage.
        This is the only safe way to obtain a page for a request.
        """
        # 1. Reject if shutting down
        if self.engine.is_shutting_down:
            raise RuntimeError("BrowserEngine is shutting down")

        # 2. Semaphore Acquisition (Enforce limit before any browser work)
        await self.semaphore.acquire()
        
        try:
            # 3. Ensure health (Lazy init or recovery)
            await self.ensure_healthy()
            
            # 4. Bounded Page Creation
            # Wrap context.new_page() with a timeout to prevent hanging.
            page = await asyncio.wait_for(self.context.new_page(), timeout=10.0)
            
            return ManagedPage(page, self)
        except Exception:
            # Release permit if page creation fails or times out
            self.semaphore.release()
            raise

    async def ensure_healthy(self):
        """Self-healing logic to recover from crashes or discarded tabs."""
        async with self.init_lock:
            # 1. Ensure the core browser process is healthy first
            # This handles cases where the browser crashed or hasn't been initialized.
            async with self.engine.management_lock:
                await self.engine._ensure_healthy_browser()

            # 2. Check if the session context is still valid
            needs_setup = not self.is_alive
            
            if not needs_setup:
                try:
                    # Probe keepalive tab responsiveness with bounded timeout
                    if not self.keepalive_page or self.keepalive_page.is_closed():
                        needs_setup = True
                    else:
                        await asyncio.wait_for(self.keepalive_page.evaluate("1"), timeout=2.0)
                except (asyncio.TimeoutError, Exception) as e:
                    logger.warning(f"ProviderSession({self.name}): Health probe failed: {e}")
                    needs_setup = True

            if needs_setup:
                await self._setup()

    async def _setup(self):
        """Initializes context, keepalive tab, and autosave loop."""
        # Clean shutdown of previous resources (if any)
        await self.close_resources(save_state=False)

        context_args = {
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }

        if self._validate_state_file():
            context_args["storage_state"] = self.state_path
            logger.info(f"ProviderSession({self.name}): Loading session state.")

        try:
            self.context = await self.engine.browser.new_context(**context_args)
            self.keepalive_page = await self.context.new_page()
            self.last_browser_generation = self.engine.browser_generation
            
            # Navigate to stable URI
            data_uri = f"data:text/html,<html><head><title>keepalive-{self.name}</title></head><body>keepalive-{self.name}</body></html>"
            await self.keepalive_page.goto(data_uri, wait_until="domcontentloaded")
            
            # Verify renderer responsiveness
            await asyncio.wait_for(self.keepalive_page.evaluate("document.title"), timeout=2.0)
            
            # Start single autosave task
            if not self.engine.is_shutting_down:
                self.autosave_task = asyncio.create_task(self._autosave_loop())
            
            logger.info("provider_session_initialized", extra={"provider": self.name, "gen": self.last_browser_generation})
        except Exception as e:
            logger.error(f"ProviderSession({self.name}): Initialization failed: {e}")
            await self.close_resources(save_state=False)
            raise

    def _validate_state_file(self) -> bool:
        """Validates JSON structure to prevent Playwright startup crashes."""
        if not os.path.exists(self.state_path):
            return False
        try:
            if os.path.getsize(self.state_path) == 0:
                return False
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return "cookies" in data or "origins" in data
        except Exception as e:
            ts = int(time.time())
            logger.error(f"ProviderSession({self.name}): Corrupted state detected: {e}")
            try: os.rename(self.state_path, f"{self.state_path}.corrupted.{ts}")
            except Exception: pass
            return False

    async def _autosave_loop(self):
        """Periodic checkpointing task."""
        try:
            while not self.engine.is_shutting_down:
                await asyncio.sleep(60)
                if self.is_alive:
                    await self.save_state()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"ProviderSession({self.name}): Autosave loop crashed: {e}")

    async def save_state(self):
        """Atomically persists storage state to disk."""
        if not self.is_alive:
            return

        async with self.state_lock:
            tmp_path = f"{self.state_path}.tmp"
            try:
                await self.context.storage_state(path=tmp_path)
                with open(tmp_path, "rb+") as f:
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self.state_path)
            except Exception as e:
                if "Target closed" not in str(e):
                    logger.warning(f"ProviderSession({self.name}): Persistence failed: {e}")
                if os.path.exists(tmp_path):
                    try: os.remove(tmp_path)
                    except Exception: pass

    async def close_resources(self, save_state: bool = True):
        """Deterministic resource teardown."""
        if save_state:
            await self.save_state()

        if self.autosave_task:
            self.autosave_task.cancel()
            try:
                await self.autosave_task
            except (asyncio.CancelledError, Exception):
                pass
            self.autosave_task = None

        if self.keepalive_page:
            try:
                if not self.keepalive_page.is_closed():
                    await self.keepalive_page.close()
            except Exception:
                pass
            self.keepalive_page = None

        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass
            self.context = None
            logger.info("provider_session_closed", extra={"provider": self.name})

class BrowserEngine:
    """
    Production-grade Browser Engine.
    Orchestrates a singleton Browser process and multiple ProviderSessions.
    """
    _instance: Optional['BrowserEngine'] = None
    _lock = asyncio.Lock() # For singleton protection

    def __init__(self):
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.browser_generation = 0
        
        # Concurrency & Sessions
        self.sessions: Dict[str, ProviderSession] = {}
        self.sessions_lock = asyncio.Lock()    # Protects sessions dictionary
        self.management_lock = asyncio.Lock()  # Serializes browser recovery
        
        # Configuration
        self.user_data_dir = os.path.join(os.getcwd(), ".playwright_data")
        os.makedirs(self.user_data_dir, exist_ok=True)
        
        self.headless = CONFIG["Playwright"].getboolean("headless", False)
        self.max_pages = CONFIG["Playwright"].getint("max_concurrent_pages", 5)
        self.is_shutting_down = False
        self.recovery_count = 0

    @classmethod
    async def get_instance(cls) -> 'BrowserEngine':
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    async def get_session(self, provider_name: str) -> ProviderSession:
        """Returns or initializes a ProviderSession in a thread-safe manner."""
        async with self.sessions_lock:
            if provider_name not in self.sessions:
                self.sessions[provider_name] = ProviderSession(self, provider_name)
            return self.sessions[provider_name]

    async def get_page(self, provider: str = "gemini") -> ManagedPage:
        """Entry point for requests to obtain a new tab."""
        async with self.management_lock:
            await self._ensure_healthy_browser()
        
        session = await self.get_session(provider)
        return await session.get_page()

    async def _ensure_healthy_browser(self):
        """Maintains the browser process and tracks its generation."""
        if not self.playwright or not self.browser or not self.browser.is_connected():
            logger.info("BrowserEngine: Initializing Browser...")
            
            # Ensure full cleanup of stale resources
            try:
                if self.browser:
                    await self.browser.close()
            except Exception: pass

            try:
                if self.playwright:
                    await self.playwright.stop()
            except Exception: pass
            
            try:
                self.playwright = await async_playwright().start()
                self.browser = await self.playwright.chromium.launch(
                    headless=self.headless,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                    ]
                )
                self.browser_generation += 1
                self.recovery_count += 1
                logger.info("BrowserEngine: New generation active.", extra={"gen": self.browser_generation})
            except Exception as e:
                logger.error(f"BrowserEngine: Failed to launch browser: {e}")
                self.browser = None
                raise

    async def is_authenticated(self, page: Page) -> bool:
        """Reliable fail-open auth check via direct DOM evaluation."""
        try:
            url = page.url
            if "accounts.google.com" in url and "/signin" in url:
                return False
            
            # Targeted selector check
            signin_button = page.get_by_role("button", name=re.compile(r"sign in", re.IGNORECASE)).first
            try:
                # Direct DOM probe is more deterministic than is_visible
                visible = await asyncio.wait_for(
                    signin_button.evaluate("el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)"),
                    timeout=1.5
                )
                if visible: return False
            except (asyncio.TimeoutError, Exception):
                pass
            return True
        except Exception as e:
            if "Target closed" in str(e): return True
            logger.warning(f"Auth health check error (fail-open): {e}")
            return True

    @property
    def active_pages(self) -> int:
        """Aggregated count of active request tabs across all providers."""
        return sum(s.active_pages for s in self.sessions.values())

    async def close(self) -> None:
        """Graceful shutdown sequence with bounded request drain."""
        async with self.management_lock:
            if self.is_shutting_down:
                return
            
            logger.info("BrowserEngine: Shutting down...")
            self.is_shutting_down = True
            
            # 1. Bounded Request Drain
            # Wait for up to 15 seconds for active pages to finish.
            drain_start = time.time()
            drain_timeout = 15.0
            while self.active_pages > 0 and (time.time() - drain_start) < drain_timeout:
                logger.info(f"BrowserEngine: Draining active pages ({self.active_pages} left)...")
                await asyncio.sleep(1.0)
            
            if self.active_pages > 0:
                logger.warning(f"BrowserEngine: Draining timed out with {self.active_pages} pages remaining.")

            # 2. Close all sessions (cancels tasks, closes contexts)
            # This will also save state since we are doing a graceful shutdown.
            for session in list(self.sessions.values()):
                await session.close_resources(save_state=True)
            
            # 3. Cleanup browser process
            if self.browser:
                try: await self.browser.close()
                except Exception: pass
            
            # 4. Cleanup playwright driver
            if self.playwright:
                try: await self.playwright.stop()
                except Exception: pass
            
            self.sessions.clear()
            self.browser = None
            self.playwright = None
            logger.info("BrowserEngine: Shutdown complete.")

async def get_browser_engine() -> BrowserEngine:
    return await BrowserEngine.get_instance()
