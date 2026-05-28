import os
import asyncio
import json
import time
import re
from typing import Optional
from playwright.async_api import async_playwright, Playwright, BrowserContext, Page, Browser
from app.logger import logger
from app.config import CONFIG

class BrowserEngine:
    """
    Production-grade Browser Engine.
    Manages a SINGLE shared BrowserContext for all requests (Tabs vs Windows).
    Supports atomic state persistence (cookies/login) and self-healing.
    """
    _instance: Optional['BrowserEngine'] = None
    _lock = asyncio.Lock()

    def __init__(self):
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        
        # Shared Context Management (Tabs strategy)
        self.context: Optional[BrowserContext] = None
        self.keepalive_page: Optional[Page] = None
        self.keepalive_lock = asyncio.Lock()
        
        # Persistent data directory for cookie/auth extraction
        self.user_data_dir = os.path.join(os.getcwd(), ".playwright_data")
        self.state_path = os.path.join(self.user_data_dir, "state.json")
        os.makedirs(self.user_data_dir, exist_ok=True)
        
        # State Safety Controls
        self.state_lock = asyncio.Lock()
        self.state_autosave_task: Optional[asyncio.Task] = None
        self.is_shutting_down = False
        
        # Load config
        self.headless = CONFIG["Playwright"].getboolean("headless", False)
        self.max_pages = CONFIG["Playwright"].getint("max_concurrent_pages", 5)
        
        # Concurrency control
        self.semaphore = asyncio.Semaphore(self.max_pages)
        self.management_lock = asyncio.Lock()
        
        # Metrics
        self.recovery_count = 0

    @property
    def active_pages(self) -> int:
        """Total active request pages (excludes keepalive)."""
        if self.context:
            try:
                # Subtract 1 for the permanent keepalive page
                return max(0, len(self.context.pages) - 1)
            except Exception:
                pass
        return 0

    @classmethod
    async def get_instance(cls) -> 'BrowserEngine':
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    async def get_page(self) -> Page:
        """
        Returns a new isolated tab (page) within the shared context.
        Ensures the browser and context are healthy.
        """
        async with self.management_lock:
            await self._ensure_healthy_browser()
            return await self.context.new_page()

    async def _ensure_healthy_browser(self):
        """Ensures Playwright, Browser, and the shared Context are alive and healthy."""
        browser_reinitialized = False
        if not self.playwright or not self.browser or not self.browser.is_connected():
            logger.info("BrowserEngine: Initializing Browser instance...")
            if self.playwright: 
                try: 
                    await self.playwright.stop()
                except Exception as e: 
                    logger.warning(f"BrowserEngine: Error stopping previous playwright instance: {e}")
            
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
            browser_reinitialized = True
            self.recovery_count += 1

        # Shared context and keepalive health check (Self-healing)
        needs_context_setup = browser_reinitialized or not self.context
        
        if not needs_context_setup:
            # Check context health
            try:
                await self.browser.version() # Browser still connected?
                if not self.keepalive_page or self.keepalive_page.is_closed():
                    logger.warning("keepalive_health_failed", extra={"reason": "missing_or_closed"})
                    needs_context_setup = True
                else:
                    # Bounded connectivity check
                    await asyncio.wait_for(self.keepalive_page.evaluate("1"), timeout=2.0)
            except Exception as e:
                logger.warning("context_health_failed", extra={"error": str(e)})
                needs_context_setup = True

        if needs_context_setup:
            await self._setup_shared_context()

    async def _setup_shared_context(self):
        """Initializes the single shared context and permanent keepalive tab."""
        async with self.keepalive_lock:
            # Cleanup old resources if they exist
            if self.state_autosave_task and not self.state_autosave_task.done():
                self.state_autosave_task.cancel()
                try: await self.state_autosave_task
                except asyncio.CancelledError: pass
            
            if self.keepalive_page:
                try: await self.keepalive_page.close()
                except Exception: pass
            if self.context:
                try: await self.context.close()
                except Exception: pass

            context_args = {
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            }
            
            if self._validate_state_file():
                context_args["storage_state"] = self.state_path
                logger.info(f"BrowserEngine: Loading state from {self.state_path}")

            try:
                self.context = await self.browser.new_context(**context_args)
                self.keepalive_page = await self.context.new_page()
                
                # Use stable lightweight data URI with a title to prevent suspension
                data_uri = "data:text/html,<html><head><title>keepalive</title></head><body>keepalive</body></html>"
                await self.keepalive_page.goto(data_uri, wait_until="domcontentloaded")
                
                # Post-creation validation
                await asyncio.wait_for(self.keepalive_page.evaluate("document.title"), timeout=2.0)
                
                # Start autosave for the shared context
                if not self.is_shutting_down:
                    self.state_autosave_task = asyncio.create_task(self._autosave_loop())
                
                logger.info("shared_context_initialized", extra={"active_pages": self.active_pages})
            except Exception as e:
                logger.error(f"BrowserEngine: Failed to setup shared context: {e}")
                self.context = None
                self.keepalive_page = None
                raise

    def _validate_state_file(self) -> bool:
        """Validates the state.json file before loading."""
        if not os.path.exists(self.state_path):
            return False
            
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            if "cookies" in data or "origins" in data:
                logger.info("state_load_success", extra={"file": self.state_path, "size": os.path.getsize(self.state_path)})
                return True
            else:
                raise ValueError("Missing 'cookies' or 'origins' keys")
        except Exception as e:
            ts = int(time.time())
            corrupted_path = f"{self.state_path}.corrupted.{ts}"
            logger.error("state_load_invalid", extra={"error": str(e), "file": self.state_path})
            try:
                os.rename(self.state_path, corrupted_path)
                logger.warning(f"BrowserEngine: Renamed corrupted state to {corrupted_path}")
            except Exception as rename_err:
                logger.error(f"BrowserEngine: Failed to rename corrupted state: {rename_err}")
            return False

    async def _autosave_loop(self):
        """Periodic background task to checkpoint shared session state."""
        try:
            while not self.is_shutting_down:
                await asyncio.sleep(60)
                if self.context and self.browser and self.browser.is_connected():
                    await self._atomic_save_state()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"BrowserEngine: Autosave loop error: {e}")

    async def _atomic_save_state(self):
        """Atomically saves the shared context storage state."""
        if not self.context or not self.browser or not self.browser.is_connected():
            return

        async with self.state_lock:
            tmp_path = f"{self.state_path}.tmp"
            try:
                await self.context.storage_state(path=tmp_path)
                
                with open(tmp_path, "rb+") as f:
                    f.flush()
                    os.fsync(f.fileno())
                
                os.replace(tmp_path, self.state_path)
                logger.info("state_save_success", extra={"size": os.path.getsize(self.state_path)})
            except Exception as e:
                if "Target closed" in str(e) or "Browser closed" in str(e):
                    return
                logger.warning("state_save_failure", extra={"error": str(e)})
                if os.path.exists(tmp_path):
                    try: os.remove(tmp_path)
                    except Exception: pass

    async def save_state(self):
        """Public method to manually trigger state saving."""
        if self.context and not self.is_shutting_down:
            await self._atomic_save_state()

    async def is_authenticated(self, page: Page) -> bool:
        """
        Reliable health check for authentication state.
        Uses a fail-open strategy and direct DOM evaluation.
        """
        try:
            url = page.url
            if "accounts.google.com" in url and "/signin" in url:
                logger.warning("Auth health check: Direct sign-in URL detected.")
                return False
            
            signin_button = page.get_by_role("button", name=re.compile(r"sign in", re.IGNORECASE)).first
            
            try:
                # Optimized visibility check via direct DOM evaluation
                visible = await asyncio.wait_for(
                    signin_button.evaluate(
                        "el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)"
                    ),
                    timeout=1.5
                )
                if visible:
                    logger.warning("Auth health check: 'Sign in' button is visible.")
                    return False
            except asyncio.TimeoutError:
                logger.debug("Auth health check visibility timeout (fail-open).")
            except Exception as e:
                logger.debug(f"Auth health check evaluation skipped: {e}")
                
            return True
        except Exception as e:
            if "Target closed" in str(e) or "Browser closed" in str(e):
                return True 
            logger.warning(f"BrowserEngine: Non-fatal auth check error (fail-open): {e}")
            return True

    async def notify_page_closed(self, page: Page):
        """Callback for pages to notify their closure. No-op in shared context model."""
        pass

    async def close(self) -> None:
        """Clean and orderly shutdown of all browser resources."""
        async with self.management_lock:
            logger.info("BrowserEngine: Shutting down...")
            self.is_shutting_down = True
            
            # 1. Stop autosave task
            if self.state_autosave_task and not self.state_autosave_task.done():
                self.state_autosave_task.cancel()
                try: await self.state_autosave_task
                except asyncio.CancelledError: pass

            # 2. Final atomic save
            if self.context:
                await self._atomic_save_state()
            
            # 3. Close keepalive tab
            if self.keepalive_page and not self.keepalive_page.is_closed():
                try: await self.keepalive_page.close()
                except Exception: pass
            
            # 4. Close shared context
            if self.context:
                try: await self.context.close()
                except Exception: pass
            
            # 5. Shutdown browser and playwright
            if self.browser:
                try: await self.browser.close()
                except Exception: pass
            
            if self.playwright:
                try: await self.playwright.stop()
                except Exception: pass
            
            self.context = None
            self.keepalive_page = None
            self.browser = None
            self.playwright = None
            logger.info("BrowserEngine: Shutdown complete.")

async def get_browser_engine() -> BrowserEngine:
    return await BrowserEngine.get_instance()
