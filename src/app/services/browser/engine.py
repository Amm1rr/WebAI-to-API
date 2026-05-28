import os
import asyncio
import json
import time
import re
from typing import Optional, Dict
from playwright.async_api import async_playwright, Playwright, BrowserContext, Page, Browser
from app.logger import logger
from app.config import CONFIG

class BrowserEngine:
    """
    Production-grade Browser Engine.
    Manages a single Browser instance with generation-based Context Rotation.
    Supports state persistence (cookies/login) and self-healing.
    """
    _instance: Optional['BrowserEngine'] = None
    _lock = asyncio.Lock()

    def __init__(self):
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        
        # Isolated Keepalive Management
        self.keepalive_context: Optional[BrowserContext] = None
        self.keepalive_page: Optional[Page] = None
        self.keepalive_lock = asyncio.Lock()
        
        # Generation-based Request Context Management
        self.active_context: Optional[BrowserContext] = None
        self.retiring_contexts: Dict[BrowserContext, int] = {} # context -> active_page_count
        self.context_generation = 0
        
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
        self.context_rotation_count = 0

    @property
    def active_pages(self) -> int:
        """Total active request pages (excludes keepalive)."""
        total = 0
        if self.active_context:
            try: total += len(self.active_context.pages)
            except Exception: pass
        for ctx in self.retiring_contexts:
            try: total += len(ctx.pages)
            except Exception: pass
        return total

    @classmethod
    async def get_instance(cls) -> 'BrowserEngine':
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    async def get_page(self) -> Page:
        """
        Returns a new isolated request page. 
        Triggers rotation if active context is unhealthy.
        """
        async with self.management_lock:
            await self._ensure_healthy_browser()
            await self._ensure_healthy_context()
            return await self.active_context.new_page()

    async def _ensure_healthy_browser(self):
        """Ensures the Playwright, Browser, and Keepalive context are alive and healthy."""
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

        # Health check for keepalive resources (Self-healing)
        needs_keepalive_recreation = browser_reinitialized
        if not needs_keepalive_recreation:
            # Simplified health check focusing on the page object
            if not self.keepalive_page or self.keepalive_page.is_closed():
                logger.warning("keepalive_health_failed", extra={"reason": "missing_or_closed"})
                needs_keepalive_recreation = True
            else:
                try:
                    # Bounded connectivity check to detect discarded/frozen tabs
                    await asyncio.wait_for(self.keepalive_page.evaluate("1"), timeout=2.0)
                except asyncio.TimeoutError:
                    logger.warning("keepalive_health_failed", extra={"reason": "timeout"})
                    needs_keepalive_recreation = True
                except Exception as e:
                    logger.warning("keepalive_health_failed", extra={"reason": "eval_failed", "error": str(e)})
                    needs_keepalive_recreation = True

        if needs_keepalive_recreation:
            async with self.keepalive_lock:
                # Double-check after acquiring lock to prevent duplicate recreation
                if not browser_reinitialized and self.keepalive_page and not self.keepalive_page.is_closed():
                    try:
                        await asyncio.wait_for(self.keepalive_page.evaluate("1"), timeout=2.0)
                        logger.info("keepalive_recreation_skipped", extra={"reason": "already_restored"})
                        return
                    except asyncio.TimeoutError:
                        logger.warning("keepalive_recreation_doublecheck_failed", extra={"reason": "timeout"})
                    except Exception as e:
                        logger.warning("keepalive_recreation_doublecheck_failed", extra={"reason": "eval_failed", "error": str(e)})
                
                logger.info("keepalive_recreation_started")
                await self._create_keepalive_context()
                logger.info("keepalive_recreation_completed")
                if not browser_reinitialized:
                    logger.info("keepalive_recreated")
                logger.info("keepalive_health_restored")

    async def _create_keepalive_context(self):
        """Initializes a dedicated keepalive context and page with environmental stability fixes."""
        # Cleanup old resources if they exist but were deemed unhealthy
        if self.keepalive_page:
            try: await self.keepalive_page.close()
            except Exception: pass
        if self.keepalive_context:
            try: await self.keepalive_context.close()
            except Exception: pass
            
        try:
            self.keepalive_context = await self.browser.new_context()
            self.keepalive_page = await self.keepalive_context.new_page()
            
            # Use stable lightweight data URI with a title to prevent suspension in Linux environments
            data_uri = "data:text/html,<html><head><title>keepalive</title></head><body>keepalive</body></html>"
            await self.keepalive_page.goto(data_uri, wait_until="domcontentloaded")
            
            # Post-creation validation to ensure renderer is responsive
            await asyncio.wait_for(self.keepalive_page.evaluate("document.title"), timeout=2.0)
            logger.info("keepalive_context_created")
        except Exception as e:
            logger.error(f"BrowserEngine: Failed to create keepalive context: {e}")
            # Ensure we don't leave partial/broken state
            if self.keepalive_page:
                try: await self.keepalive_page.close()
                except Exception: pass
            if self.keepalive_context:
                try: await self.keepalive_context.close()
                except Exception: pass
            self.keepalive_page = None
            self.keepalive_context = None
            raise

    async def _ensure_healthy_context(self):
        """Ensures an active request context is available and healthy."""
        needs_rotation = False
        if not self.active_context:
            needs_rotation = True
        else:
            try: 
                await self.active_context.browser.version()
            except Exception as e:
                logger.warning(f"BrowserEngine: Request context health check failed: {e}")
                needs_rotation = True
        
        if needs_rotation:
            await self.rotate_context()

    def _validate_state_file(self) -> bool:
        """Validates the state.json file before loading to prevent corruption crashes."""
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

    async def rotate_context(self):
        """
        Rotates the active request context. 
        Old context is moved to 'retiring' until its pages are closed.
        Loads existing login state if available and valid.
        """
        if self.state_autosave_task and not self.state_autosave_task.done():
            self.state_autosave_task.cancel()
            try:
                await self.state_autosave_task
                logger.info("autosave_cancelled_cleanly", extra={"reason": "context_rotation"})
            except asyncio.CancelledError:
                logger.info("autosave_cancelled_cleanly", extra={"reason": "context_rotation"})
            except Exception as e:
                logger.warning(f"BrowserEngine: Error cancelling autosave task: {e}")
        self.state_autosave_task = None
        
        if self.active_context:
            self.retiring_contexts[self.active_context] = len(self.active_context.pages)
            await self._atomic_save_state(self.active_context)
        
        self.context_generation += 1
        self.context_rotation_count += 1
        
        context_args = {
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }
        
        if self._validate_state_file():
            context_args["storage_state"] = self.state_path
            logger.info(f"BrowserEngine: Loading state from {self.state_path}")

        self.active_context = await self.browser.new_context(**context_args)
        
        logger.info("request_context_rotated", extra={
            "context_generation": self.context_generation,
            "active_pages": self.active_pages
        })
        
        if not self.is_shutting_down:
            self.state_autosave_task = asyncio.create_task(self._autosave_loop())
            logger.info("autosave_started", extra={"interval": 60})

    async def _autosave_loop(self):
        """Periodic background task to checkpoint session state."""
        try:
            while not self.is_shutting_down:
                await asyncio.sleep(60)
                if self.active_context and self.browser and self.browser.is_connected():
                    await self._atomic_save_state(self.active_context)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"BrowserEngine: Autosave loop encountered an error: {e}")

    async def _atomic_save_state(self, context: BrowserContext):
        """Atomically saves the storage state to prevent file corruption."""
        if not context.browser or not context.browser.is_connected():
            logger.info("state_save_skipped_closed_context")
            return

        async with self.state_lock:
            tmp_path = f"{self.state_path}.tmp"
            try:
                await context.storage_state(path=tmp_path)
                
                # fsync to ensure data is actually written to physical disk
                with open(tmp_path, "rb+") as f:
                    f.flush()
                    os.fsync(f.fileno())
                
                # Atomic replace
                os.replace(tmp_path, self.state_path)
                
                size = os.path.getsize(self.state_path)
                logger.info("state_save_success", extra={"file": self.state_path, "size": size})
            except Exception as e:
                # Suppress target closed errors
                if "Target closed" in str(e) or "Browser closed" in str(e):
                    logger.info("state_save_skipped_closed_context")
                    return

                logger.warning("state_save_failure", extra={"error": str(e)})
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass

    async def save_state(self):
        """Public method to manually trigger state saving for the active context only."""
        if self.active_context and not self.is_shutting_down:
            await self._atomic_save_state(self.active_context)

    async def is_authenticated(self, page: Page) -> bool:
        """
        Reliable health check for authentication state.
        Uses a fail-open strategy and bounded timeouts to prevent unnecessary rotations.
        """
        try:
            url = page.url
            if "accounts.google.com" in url and "/signin" in url:
                logger.warning("Auth health check: Direct sign-in URL detected.")
                return False
            
            # Heuristic check for Guest mode "Sign in" buttons
            # Use a bounded wait_for to prevent hanging during transient DOM states
            signin_button = page.get_by_role("button", name=re.compile(r"sign in", re.IGNORECASE)).first
            
            try:
                # Optimized visibility check via direct DOM evaluation
                # Bypasses Playwright's auto-wait pipeline for better RPC efficiency
                visible = await asyncio.wait_for(
                    signin_button.evaluate(
                        """
                        el => !!(
                            el.offsetWidth ||
                            el.offsetHeight ||
                            el.getClientRects().length
                        )
                        """
                    ),
                    timeout=1.5
                )
                if visible:
                    logger.warning("Auth health check: 'Sign in' button is visible. Guest mode detected.")
                    return False
            except asyncio.TimeoutError:
                logger.debug("Auth health check visibility evaluation timed out (fail-open).")
            except Exception as e:
                logger.debug(f"Auth health check: Optional evaluation check skipped: {e}")
                
            return True
        except Exception as e:
            # If the page or target is closed mid-check, do NOT invalidate the session.
            if "Target closed" in str(e) or "Browser closed" in str(e):
                return True 

            logger.warning(f"BrowserEngine: Non-fatal auth check error (fail-open): {e}")
            return True

    async def notify_page_closed(self, page: Page):
        """Callback for pages to notify their closure, allowing for context cleanup."""
        async with self.management_lock:
            context = page.context
            
            # Explicitly exclude keepalive context from retirement logic
            if self.keepalive_context and context == self.keepalive_context:
                return

            if context in self.retiring_contexts:
                if len(context.pages) == 0:
                    try: 
                        await context.close()
                    except Exception as e: 
                        logger.warning(f"BrowserEngine: Error closing retiring context: {e}")
                    del self.retiring_contexts[context]

    async def close(self) -> None:
        """Clean and orderly shutdown of all browser resources."""
        async with self.management_lock:
            logger.info("BrowserEngine: Shutting down...")
            self.is_shutting_down = True
            
            # 1. Stop autosave task
            if self.state_autosave_task and not self.state_autosave_task.done():
                self.state_autosave_task.cancel()
                logger.info("autosave_stopped", extra={"reason": "shutdown"})
                try:
                    await self.state_autosave_task
                except asyncio.CancelledError:
                    pass

            # 2. Final atomic save of request session
            if self.active_context:
                await self._atomic_save_state(self.active_context)
            
            # 3. Close request contexts
            if self.active_context:
                try: 
                    await self.active_context.close()
                except Exception as e: 
                    logger.warning(f"BrowserEngine: Error closing active context: {e}")
            
            for ctx in list(self.retiring_contexts.keys()):
                try: 
                    await ctx.close()
                except Exception as e: 
                    logger.warning(f"BrowserEngine: Error closing retiring context: {e}")
            
            # 4. Close isolated keepalive resources
            if self.keepalive_page and not self.keepalive_page.is_closed():
                try:
                    await self.keepalive_page.close()
                except Exception as e:
                    logger.warning(f"BrowserEngine: Error closing keepalive page: {e}")
            
            if self.keepalive_context:
                try:
                    await self.keepalive_context.close()
                    logger.info("keepalive_context_closed")
                except Exception as e:
                    logger.warning(f"BrowserEngine: Error closing keepalive context: {e}")
            
            # 5. Shutdown browser and playwright
            if self.browser:
                try: 
                    await self.browser.close()
                except Exception as e: 
                    logger.warning(f"BrowserEngine: Error closing browser: {e}")
            
            if self.playwright:
                try: 
                    await self.playwright.stop()
                except Exception as e: 
                    logger.warning(f"BrowserEngine: Error stopping playwright: {e}")
            
            self.active_context = None
            self.retiring_contexts = {}
            self.keepalive_context = None
            self.keepalive_page = None
            self.browser = None
            self.playwright = None
            logger.info("BrowserEngine: Shutdown complete.")

async def get_browser_engine() -> BrowserEngine:
    return await BrowserEngine.get_instance()
