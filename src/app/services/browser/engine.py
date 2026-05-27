import os
import asyncio
from typing import Optional
from playwright.async_api import async_playwright, Playwright, BrowserContext, Page
from app.logger import logger
from app.config import CONFIG

import os
import asyncio
from typing import Optional, Dict, Set
from playwright.async_api import async_playwright, Playwright, BrowserContext, Page, Browser
from app.logger import logger
from app.config import CONFIG

class BrowserEngine:
    """
    Production-grade Browser Engine.
    Manages a single Browser instance with generation-based Context Rotation.
    Supports self-healing without interrupting active streams.
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
            total += len(self.active_context.pages)
        for ctx in self.retiring_contexts:
            total += len(ctx.pages)
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
            
            page = await self.active_context.new_page()
            return page

    async def _ensure_healthy_browser(self):
        """Ensures the Playwright and Browser instances are alive."""
        if not self.playwright or not self.browser or not self.browser.is_connected():
            logger.info("BrowserEngine: Initializing/Recovering Browser instance...")
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
        """
        if self.active_context:
            logger.info(f"BrowserEngine: Retiring context generation {self.context_generation}")
            self.retiring_contexts[self.active_context] = len(self.active_context.pages)
        
        self.context_generation += 1
        self.context_rotation_count += 1
        
        # Create new context with persistent state injection if needed
        # For now, we use a simple context, but could load storageState here
        self.active_context = await self.browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        # Inject cookies from browser_data if they exist
        # TODO: Implement robust storageState persistence/loading
        
        logger.info(f"BrowserEngine: New context generation {self.context_generation} active.")

    async def notify_page_closed(self, page: Page):
        """Handles context cleanup after a page is closed."""
        async with self.management_lock:
            context = page.context
            if context in self.retiring_contexts:
                # Check if this context can now be closed
                if len(context.pages) == 0:
                    logger.info(f"BrowserEngine: Closing retired context with 0 pages.")
                    await context.close()
                    del self.retiring_contexts[context]

    async def close(self) -> None:
        """Graceful global shutdown."""
        async with self.management_lock:
            logger.info("BrowserEngine: Shutting down...")
            if self.active_context:
                await self.active_context.close()
            for ctx in list(self.retiring_contexts.keys()):
                await ctx.close()
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
            logger.info("BrowserEngine: Shutdown complete.")

async def get_browser_engine() -> BrowserEngine:
    return await BrowserEngine.get_instance()

async def get_browser_engine() -> BrowserEngine:
    """Entry point to retrieve the singleton BrowserEngine instance."""
    return await BrowserEngine.get_instance()
