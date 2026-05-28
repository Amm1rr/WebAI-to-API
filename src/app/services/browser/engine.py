import os
import asyncio
import json
import time
import re
import uuid
from enum import Enum
from typing import Optional, Dict, Any, List
from collections import OrderedDict
from playwright.async_api import async_playwright, Playwright, BrowserContext, Page, Browser, Error as PlaywrightError
from app.logger import logger
from app.config import CONFIG

class TabStatus(Enum):
    """Lifecycle states for a browser tab."""
    IDLE = "idle"             # In registry, ready for reuse
    LEASED = "leased"         # Active in a request, owned by a lease_token
    INVALIDATING = "invalidating" # Transitioning to shutdown, no new leases allowed
    DEAD = "dead"             # Closed or invalid, should be removed from registry

class PersistentTab:
    """
    Represents a browser tab that persists across multiple API requests.
    Encapsulates its own locking and ownership logic.
    """
    def __init__(self, page: Page, conversation_id: str, generation: int):
        self.page = page
        self.conversation_id = conversation_id
        self.browser_generation = generation
        self.status = TabStatus.IDLE
        self.created_at = time.time()
        self.last_used_at = self.created_at
        
        # Ownership Tracking
        self.lease_token: Optional[str] = None
        self.owner_request_id: Optional[str] = None
        self.leased_at: Optional[float] = None
        
        self._lock = asyncio.Lock() # Private lock, managed via acquire/release methods

    def is_valid(self, current_gen: int) -> bool:
        """Determines if the tab is viable for a new lease."""
        return (
            self.status == TabStatus.IDLE and
            self.browser_generation == current_gen and
            not self.page.is_closed()
        )

    async def acquire_lease(self, request_id: str) -> Optional[str]:
        """
        Attempts to acquire an exclusive lease for a request.
        Returns a unique lease_token on success, None otherwise.
        """
        # Note: External caller must ensure registry_lock is NOT held while awaiting this
        await self._lock.acquire()
        
        # Re-validate state AND physical page AFTER acquiring the lock
        if self.status != TabStatus.IDLE or self.page.is_closed():
            self._lock.release()
            return None
            
        token = str(uuid.uuid4())
        self.status = TabStatus.LEASED
        self.lease_token = token
        self.owner_request_id = request_id
        self.leased_at = time.time()
        self.last_used_at = self.leased_at
        
        logger.debug(f"Tab({self.conversation_id}): Lease acquired by {request_id}", 
                     extra={"token": token})
        return token

    async def release_lease(self, token: str) -> bool:
        """
        Releases the lease if the token matches.
        Returns True if released, False if token mismatch or already released.
        """
        if not self._lock.locked() or self.lease_token != token:
            logger.warning(f"Tab({self.conversation_id}): Rejected lease release attempt (Token Mismatch)")
            return False

        try:
            # Only return to IDLE if we aren't being killed/invalidated
            if self.status == TabStatus.LEASED:
                self.status = TabStatus.IDLE
            
            self.lease_token = None
            self.owner_request_id = None
            self.leased_at = None
            self.last_used_at = time.time()
            return True
        finally:
            self._lock.release()

    def invalidate(self):
        """Transitions the tab to an invalid state, preventing further leases."""
        if self.status not in (TabStatus.INVALIDATING, TabStatus.DEAD):
            self.status = TabStatus.INVALIDATING

    async def close(self):
        """Safely shuts down the tab and marks it as dead."""
        if self.status == TabStatus.DEAD:
            return
        
        self.status = TabStatus.INVALIDATING
        try:
            if not self.page.is_closed():
                await self.page.close()
        except Exception:
            pass
        finally:
            self.status = TabStatus.DEAD
            logger.debug(f"Tab({self.conversation_id}): Status set to DEAD")

class ManagedPage:
    """
    A request-scoped lease on a browser page.
    Owns exactly one semaphore permit and potentially one PersistentTab lease.
    """
    def __init__(self, page: Page, session: 'ProviderSession', 
                 persistent_tab: Optional[PersistentTab] = None,
                 lease_token: Optional[str] = None):
        self.page = page
        self.session = session
        self.persistent_tab = persistent_tab
        self.lease_token = lease_token
        self._released = False
        self._lock = asyncio.Lock()
        self.acquired_at = time.time()

    async def close(self):
        """
        Idempotent release of the lease. 
        Ensures semaphore is returned and persistent tab is unlocked.
        """
        async with self._lock:
            if self._released:
                return
            self._released = True
            
            try:
                # 1. Semaphore return
                self.session.semaphore.release()
                self.session.active_lease_count = max(0, self.session.active_lease_count - 1)
                
                # 2. Lifecycle management
                if self.persistent_tab and self.lease_token:
                    # Return to registry via the tab's own release API
                    success = await self.persistent_tab.release_lease(self.lease_token)
                    if success:
                        # If tab was invalidated/killed while leased, ensure physical close now
                        if self.persistent_tab.status in (TabStatus.INVALIDATING, TabStatus.DEAD):
                            await self.persistent_tab.close()
                        
                        logger.info(f"PersistentTab({self.persistent_tab.conversation_id}): Lease released.", 
                                    extra={"provider": self.session.name, "duration": time.time() - self.acquired_at})
                    else:
                        logger.warning(f"PersistentTab({self.persistent_tab.conversation_id}): Ownership lost or already released.")
                else:
                    # One-off temporary tab: physical close
                    try:
                        if not self.page.is_closed():
                            await self.page.close()
                        logger.info("Temporary tab closed.", extra={"provider": self.session.name})
                    except Exception:
                        pass
            except Exception as e:
                logger.error(f"ManagedPage: Error during release: {e}")

class ProviderSession:
    """
    Manages isolated browser resources for a specific provider.
    Handles persistent tab registry and deterministic leasing.
    """
    def __init__(self, engine: 'BrowserEngine', name: str):
        self.engine = engine
        self.name = name
        self.context: Optional[BrowserContext] = None
        self.keepalive_page: Optional[Page] = None
        self.last_browser_generation = -1
        
        # Concurrency & Lifecycle
        self.max_pages = engine.max_pages
        self.semaphore = asyncio.Semaphore(self.max_pages)
        self.active_lease_count = 0
        
        self.init_lock = asyncio.Lock()   # For setup/re-init
        self.registry_lock = asyncio.Lock() # ONLY for trivial registry mutations
        self.state_lock = asyncio.Lock()  # For disk I/O
        
        # Conversation Registry (LRU-capable)
        self.conversation_registry: Dict[str, PersistentTab] = OrderedDict()
        self.max_conversations = CONFIG["Playwright"].getint("max_persistent_conversations", 20)
        self.idle_timeout = CONFIG["Playwright"].getint("idle_conversation_timeout", 900) # 15 mins
        self.lease_timeout = CONFIG["Playwright"].getint("lease_timeout", 180) # 3 mins
        
        # Background Tasks
        self.autosave_task: Optional[asyncio.Task] = None
        self.eviction_task: Optional[asyncio.Task] = None
        
        # Persistent state
        self.state_path = os.path.join(engine.user_data_dir, f"{name}_state.json")

    @property
    def is_alive(self) -> bool:
        return (
            self.context is not None and 
            self.engine.browser is not None and 
            self.engine.browser.is_connected() and
            self.last_browser_generation == self.engine.browser_generation
        )

    @property
    def metrics(self) -> dict:
        return {
            "provider": self.name,
            "registry_size": len(self.conversation_registry),
            "active_leases": self.active_lease_count,
            "max_capacity": self.max_conversations
        }

    async def acquire_lease(self, conversation_id: Optional[str] = None, request_id: str = "default") -> ManagedPage:
        """
        Two-phase acquisition flow:
        PHASE 1: Semaphore & Registry Lookup (In-memory)
        PHASE 2: Tab Lock Acquisition (Blocking, outside registry_lock)
        """
        if self.engine.is_shutting_down:
            raise RuntimeError("BrowserEngine is shutting down")

        # 1. Bound total request concurrency
        await self.semaphore.acquire()
        self.active_lease_count += 1
        
        try:
            await self.ensure_healthy()
            
            # 2. Conversational Reuse Flow
            if conversation_id:
                # PHASE 1: Fast Lookup
                tab = None
                stale_to_close = None
                async with self.registry_lock:
                    tab = self.conversation_registry.get(conversation_id)
                    if tab:
                        # Ensure we don't pick a tab from a dead generation
                        if tab.browser_generation != self.engine.browser_generation:
                            stale_tab = self.conversation_registry.pop(conversation_id)
                            # Check status BEFORE invalidation to detect if it's safe to close immediately
                            if stale_tab.status == TabStatus.IDLE:
                                stale_to_close = stale_tab
                            stale_tab.invalidate()
                            tab = None
                
                # Physically close stale IDLE tabs outside registry_lock
                if stale_to_close:
                    await stale_to_close.close()
                
                # PHASE 2: Blocking Tab Lock (Outside registry_lock)
                if tab:
                    token = await tab.acquire_lease(request_id)
                    if token:
                        # SUCCESS: Double-check validity after acquiring lock
                        if tab.browser_generation == self.engine.browser_generation:
                            async with self.registry_lock:
                                self.conversation_registry.move_to_end(conversation_id)
                            logger.info(f"Reusing persistent tab: {conversation_id}")
                            return ManagedPage(tab.page, self, persistent_tab=tab, lease_token=token)
                        else:
                            # Generation rollover during acquisition
                            await tab.release_lease(token)
                            async with self.registry_lock:
                                self.conversation_registry.pop(conversation_id, None)
                            await tab.close()
                    else:
                        logger.debug(f"Tab {conversation_id} busy or invalid. Falling back to new tab.")

            # 3. New Tab Flow
            page = await asyncio.wait_for(self.context.new_page(), timeout=10.0)
            return ManagedPage(page, self)

        except Exception:
            self.active_lease_count = max(0, self.active_lease_count - 1)
            self.semaphore.release()
            raise

    async def register_conversation(self, conversation_id: str, lease: ManagedPage) -> PersistentTab:
        """
        Promotes a temporary lease to a persistent conversation.
        Decoupled from registry_lock to avoid deadlocks.
        """
        # Note: A new tab is already exclusively 'owned' by the caller
        tab = PersistentTab(lease.page, conversation_id, self.engine.browser_generation)
        
        # 1. Pre-lock the tab before putting it in registry (Ownership Transfer)
        token = await tab.acquire_lease("internal_registration")
        if not token:
            raise RuntimeError("Failed to lock new tab for registration")
        
        to_close = []
        async with self.registry_lock:
            # Handle duplicate/collision
            if conversation_id in self.conversation_registry:
                old_tab = self.conversation_registry.pop(conversation_id)
                # Check status BEFORE invalidating to determine if we can close it
                if old_tab.status == TabStatus.IDLE:
                    to_close.append(old_tab)
                old_tab.invalidate()

            # LRU Limit Enforcement
            while len(self.conversation_registry) >= self.max_conversations:
                evicted_id = None
                for cid, t in self.conversation_registry.items():
                    if t.status == TabStatus.IDLE:
                        evicted_id = cid
                        break
                if evicted_id:
                    t = self.conversation_registry.pop(evicted_id)
                    # Check status BEFORE invalidating
                    if t.status == TabStatus.IDLE:
                        to_close.append(t)
                    t.invalidate()
                else:
                    break # All busy, allow temporary overflow

            self.conversation_registry[conversation_id] = tab
            
        # Perform physical closes outside registry_lock
        for t in to_close:
            await t.close()
            
        # 2. Update lease to point to this new persistent tab
        lease.persistent_tab = tab
        lease.lease_token = token
        logger.info(f"Registered persistent conversation: {conversation_id}")
        return tab

    async def ensure_healthy(self):
        """Self-healing: Ensures browser process and provider context are functional."""
        async with self.init_lock:
            async with self.engine.management_lock:
                await self.engine._ensure_healthy_browser()

            # Atomic Purge on generation rollover
            if self.last_browser_generation != self.engine.browser_generation:
                logger.warning(f"Browser generation rollover ({self.last_browser_generation} -> {self.engine.browser_generation})")
                await self._purge_all_tabs()

            if not self.is_alive:
                await self._setup()
            else:
                try:
                    if not self.keepalive_page or self.keepalive_page.is_closed():
                        await self._setup()
                    else:
                        await asyncio.wait_for(self.keepalive_page.evaluate("1"), timeout=2.0)
                except (asyncio.TimeoutError, Exception):
                    await self._setup()

    async def _purge_all_tabs(self):
        """Atomically clears and invalidates all tabs in registry."""
        to_close = []
        async with self.registry_lock:
            for cid in list(self.conversation_registry.keys()):
                tab = self.conversation_registry.pop(cid)
                # Check status BEFORE invalidating
                if tab.status == TabStatus.IDLE:
                    to_close.append(tab)
                tab.invalidate()
        
        for t in to_close:
            await t.close()
        logger.info(f"ProviderSession({self.name}): All tabs purged from registry.")

    async def _setup(self):
        """Full re-initialization of the provider context."""
        await self.close_resources(save_state=False)

        context_args = {
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }

        if self._validate_state_file():
            context_args["storage_state"] = self.state_path

        try:
            self.context = await self.engine.browser.new_context(**context_args)
            self.keepalive_page = await self.context.new_page()
            self.last_browser_generation = self.engine.browser_generation
            
            # Start background tasks
            if not self.engine.is_shutting_down:
                if not self.autosave_task or self.autosave_task.done():
                    self.autosave_task = asyncio.create_task(self._autosave_loop())
                if not self.eviction_task or self.eviction_task.done():
                    self.eviction_task = asyncio.create_task(self._eviction_loop())
            
            logger.info("provider_session_initialized", extra={"provider": self.name, "gen": self.last_browser_generation})
        except Exception as e:
            logger.error(f"ProviderSession({self.name}): Setup failed: {e}")
            await self.close_resources(save_state=False)
            raise

    def _validate_state_file(self) -> bool:
        if not os.path.exists(self.state_path): return False
        try:
            if os.path.getsize(self.state_path) == 0: return False
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return "cookies" in data or "origins" in data
        except Exception:
            return False

    async def _autosave_loop(self):
        try:
            while not self.engine.is_shutting_down:
                await asyncio.sleep(60)
                if self.is_alive:
                    await self.save_state()
        except asyncio.CancelledError: pass

    async def _eviction_loop(self):
        """Deterministic eviction and stale lease recovery."""
        try:
            while not self.engine.is_shutting_down:
                await asyncio.sleep(30)
                now = time.time()
                to_evict = []
                stale_recovery = []
                
                async with self.registry_lock:
                    for cid, tab in list(self.conversation_registry.items()):
                        # 1. Idle Eviction
                        if tab.status == TabStatus.IDLE and (now - tab.last_used_at > self.idle_timeout):
                            to_evict.append(cid)
                        
                        # 2. Stale Lease Recovery (Crashed Request)
                        if tab.status == TabStatus.LEASED and tab.leased_at and (now - tab.leased_at > self.lease_timeout):
                            stale_recovery.append(cid)
                
                for cid in to_evict:
                    tab_to_kill = None
                    async with self.registry_lock:
                        tab = self.conversation_registry.get(cid)
                        if tab and tab.status == TabStatus.IDLE:
                            tab_to_kill = self.conversation_registry.pop(cid)
                    
                    if tab_to_kill:
                        tab_to_kill.invalidate()
                        await tab_to_kill.close()
                        logger.info(f"Evicted idle conversation: {cid}")

                for cid in stale_recovery:
                    tab_to_kill = None
                    async with self.registry_lock:
                        tab = self.conversation_registry.get(cid)
                        if tab and tab.status == TabStatus.LEASED:
                            logger.warning(f"Recovering stale lease for CID: {cid}")
                            # ATOMIC INVALIDATION
                            tab_to_kill = self.conversation_registry.pop(cid, None)
                    
                    if tab_to_kill:
                        tab_to_kill.invalidate()
                        # The physical close ensures the page is killed even if the lock is held
                        await tab_to_kill.close()
                            
        except asyncio.CancelledError: pass

    async def save_state(self):
        if not self.is_alive: return
        async with self.state_lock:
            tmp_path = f"{self.state_path}.tmp"
            try:
                await self.context.storage_state(path=tmp_path)
                with open(tmp_path, "rb+") as f:
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self.state_path)
            except Exception:
                if os.path.exists(tmp_path):
                    try: os.remove(tmp_path)
                    except: pass

    async def close_resources(self, save_state: bool = True):
        """Teardown all session resources."""
        if save_state: await self.save_state()

        if self.autosave_task:
            self.autosave_task.cancel()
            try: await self.autosave_task
            except: pass
            self.autosave_task = None

        if self.eviction_task:
            self.eviction_task.cancel()
            try: await self.eviction_task
            except: pass
            self.eviction_task = None

        await self._purge_all_tabs()

        if self.keepalive_page:
            try:
                if not self.keepalive_page.is_closed():
                    await self.keepalive_page.close()
            except Exception: pass
            self.keepalive_page = None

        if self.context:
            try: await self.context.close()
            except Exception: pass
            self.context = None

class BrowserEngine:
    """
    Singleton manager for the browser process and provider sessions.
    """
    _instance: Optional['BrowserEngine'] = None
    _lock = asyncio.Lock()

    def __init__(self):
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.browser_generation = 0
        self.sessions: Dict[str, ProviderSession] = {}
        self.sessions_lock = asyncio.Lock()
        self.management_lock = asyncio.Lock()
        self.user_data_dir = os.path.join(os.getcwd(), ".playwright_data")
        os.makedirs(self.user_data_dir, exist_ok=True)
        self.headless = CONFIG["Playwright"].getboolean("headless", False)
        self.max_pages = CONFIG["Playwright"].getint("max_concurrent_pages", 5)
        self.is_shutting_down = False

    @classmethod
    async def get_instance(cls) -> 'BrowserEngine':
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    async def get_session(self, provider_name: str) -> ProviderSession:
        async with self.sessions_lock:
            if provider_name not in self.sessions:
                self.sessions[provider_name] = ProviderSession(self, provider_name)
            return self.sessions[provider_name]

    async def get_page(self, provider: str = "gemini") -> ManagedPage:
        session = await self.get_session(provider)
        return await session.acquire_lease()

    async def _ensure_healthy_browser(self):
        if not self.playwright or not self.browser or not self.browser.is_connected():
            logger.info("BrowserEngine: Initializing Browser...")
            if self.browser:
                try: await self.browser.close()
                except: pass
            if self.playwright:
                try: await self.playwright.stop()
                except: pass
            
            try:
                self.playwright = await async_playwright().start()
                self.browser = await self.playwright.chromium.launch(
                    headless=self.headless,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
                )
                self.browser_generation += 1
                logger.info("BrowserEngine: New generation active.", extra={"gen": self.browser_generation})
            except Exception as e:
                logger.error(f"BrowserEngine: Failed to launch browser: {e}")
                self.browser = None
                raise

    async def is_authenticated(self, page: Page) -> bool:
        try:
            if "accounts.google.com" in page.url and "/signin" in page.url:
                return False
            signin_button = page.get_by_role("button", name=re.compile(r"sign in", re.IGNORECASE)).first
            try:
                visible = await asyncio.wait_for(
                    signin_button.evaluate("el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)"),
                    timeout=1.5
                )
                if visible: return False
            except (asyncio.TimeoutError, Exception): pass
            return True
        except Exception as e:
            if "Target closed" in str(e): return True
            return True

    @property
    def active_pages(self) -> int:
        return sum(s.active_lease_count for s in self.sessions.values())

    async def close(self) -> None:
        async with self.management_lock:
            if self.is_shutting_down: return
            logger.info("BrowserEngine: Shutting down...")
            self.is_shutting_down = True
            
            drain_start = time.time()
            drain_timeout = 15.0
            while self.active_pages > 0 and (time.time() - drain_start) < drain_timeout:
                await asyncio.sleep(1.0)
            
            for session in list(self.sessions.values()):
                await session.close_resources(save_state=True)
            
            if self.browser:
                try: await self.browser.close()
                except: pass
            if self.playwright:
                try: await self.playwright.stop()
                except: pass
            
            self.sessions.clear()
            self.browser = None
            self.playwright = None
            logger.info("BrowserEngine: Shutdown complete.")

async def get_browser_engine() -> BrowserEngine:
    return await BrowserEngine.get_instance()
