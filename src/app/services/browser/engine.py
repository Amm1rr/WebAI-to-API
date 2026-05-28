import os
import asyncio
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
        
        # Generation-based Context Management
        self.active_context: Optional[BrowserContext] = None
        self.retiring_contexts: Dict[BrowserContext, int] = {} # context -> active_page_count
        self.context_generation = 0
        
        # Persistent data directory for cookie/auth extraction
        self.user_data_dir = os.path.join(os.getcwd(), ".playwright_data")
        self.state_path = os.path.join(self.user_data_dir, "state.json")
        os.makedirs(self.user_data_dir, exist_ok=True)
        
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
        """Total active pages across all contexts (active + retiring)."""
        total = 0
        if self.active_context:
            try: total += len(self.active_context.pages)
            except: pass
        for ctx in self.retiring_contexts:
            try: total += len(ctx.pages)
            except: pass
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
        Returns a new isolated page. 
        Triggers rotation if active context is unhealthy.
        """
        async with self.management_lock:
            await self._ensure_healthy_browser()
            await self._ensure_healthy_context()
            return await self.active_context.new_page()

    async def _ensure_healthy_browser(self):
        """Ensures the Playwright and Browser instances are alive."""
        if not self.playwright or not self.browser or not self.browser.is_connected():
            logger.info("BrowserEngine: Initializing Browser instance...")
            if self.playwright: 
                try: await self.playwright.stop()
                except: pass
            
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
            self.recovery_count += 1

    async def _ensure_healthy_context(self):
        """Ensures an active context is available and healthy."""
        needs_rotation = False
        if not self.active_context:
            needs_rotation = True
        else:
            try:
                # Lightweight health check
                await self.active_context.browser.version()
            except:
                needs_rotation = True
        
        if needs_rotation:
            await self.rotate_context()

    async def rotate_context(self):
        """
        Rotates the active context. 
        Old context is moved to 'retiring' until its pages are closed.
        Loads existing login state if available.
        """
        if self.active_context:
            self.retiring_contexts[self.active_context] = len(self.active_context.pages)
            await self.save_state()
        
        self.context_generation += 1
        self.context_rotation_count += 1
        
        context_args = {
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }
        
        if os.path.exists(self.state_path):
            context_args["storage_state"] = self.state_path
            logger.info(f"BrowserEngine: Loading state from {self.state_path}")

        self.active_context = await self.browser.new_context(**context_args)
        logger.info(f"BrowserEngine: Context generation {self.context_generation} active.")

    async def save_state(self):
        """Manually trigger state saving to disk."""
        if self.active_context:
            try:
                await self.active_context.storage_state(path=self.state_path)
                logger.info(f"BrowserEngine: State saved to {self.state_path}")
            except Exception as e:
                logger.warning(f"BrowserEngine: Failed to save state: {e}")

    async def notify_page_closed(self, page: Page):
        """Callback for pages to notify their closure, allowing for context cleanup."""
        async with self.management_lock:
            context = page.context
            if context in self.retiring_contexts:
                if len(context.pages) == 0:
                    try: await context.close()
                    except: pass
                    del self.retiring_contexts[context]

    async def close(self) -> None:
        """Graceful shutdown of all browser resources."""
        async with self.management_lock:
            logger.info("BrowserEngine: Shutting down...")
            await self.save_state()
            
            if self.active_context:
                try: await self.active_context.close()
                except: pass
            for ctx in list(self.retiring_contexts.keys()):
                try: await ctx.close()
                except: pass
            if self.browser:
                try: await self.browser.close()
                except: pass
            if self.playwright:
                try: await self.playwright.stop()
                except: pass
            
            self.active_context = None
            self.retiring_contexts = {}
            self.browser = None
            self.playwright = None
            logger.info("BrowserEngine: Shutdown complete.")

async def get_browser_engine() -> BrowserEngine:
    return await BrowserEngine.get_instance()
