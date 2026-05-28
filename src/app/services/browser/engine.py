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
        self.created_at = time.time() # Wall-clock for logging
        
        # INVARIANT: last_accessed_at is ONLY for LRU/Eviction (Idle timeout)
        self.last_accessed_at = time.monotonic()
        
        # INVARIANT: last_heartbeat_at is ONLY for liveness (Orphan/Stale cleanup)
        # It represents forward progress of the owning request.
        self.last_heartbeat_at = time.monotonic()
        
        # Internal flag to deduplicate detached cleanup tasks
        self._cleanup_scheduled = False
        
        # Ownership Tracking
        self.lease_token: Optional[str] = None
        self.owner_request_id: Optional[str] = None
        self.leased_at: Optional[float] = None
        
        self._lock = asyncio.Lock() # Private lock, managed via acquire/release methods

    def heartbeat(self, source: str = "unknown"):
        """
        Signals forward progress of the owning request to prevent orphan cleanup.
        INVARIANT: Heartbeat timestamps are monotonic and progress-only.
        """
        now = time.monotonic()
        if now <= self.last_heartbeat_at:
            logger.debug(f"Tab({self.conversation_id}): Heartbeat [{source}] ignored (Non-monotonic)", 
                         extra={"req_id": self.owner_request_id})
            return

        elapsed = now - self.last_heartbeat_at
        self.last_heartbeat_at = now
        logger.debug(f"Tab({self.conversation_id}): Heartbeat [{source}]", 
                     extra={"req_id": self.owner_request_id, "elapsed": f"{elapsed:.1f}s"})

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
        self.leased_at = time.monotonic()
        
        # Initialize both timestamps for the new lease
        self.last_accessed_at = self.leased_at
        self.heartbeat("initial_lease")
        
        logger.info(f"Tab({self.conversation_id}): Lease acquired", extra={"token": token, "req_id": request_id})
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
            self.last_accessed_at = time.monotonic() # Final access update
            logger.info(f"Tab({self.conversation_id}): Lease released", extra={"token": token})
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
        self.acquired_at = time.monotonic()

    async def close(self):
        """
        Idempotent release of the lease. 
        Ensures semaphore is returned and persistent tab is unlocked.
        FIX: Uses asyncio.shield to prevent lease/lock leak during cancellation.
        """
        async with self._lock:
            if self._released:
                return
            self._released = True
            
            # Execute actual release logic in a shielded block
            await asyncio.shield(self._do_close())

    async def _do_close(self):
        """Actual release implementation, shielded from cancellation."""
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
                    
                    duration = time.monotonic() - self.acquired_at
                    logger.info(f"PersistentTab({self.persistent_tab.conversation_id}): Lease released.", 
                                extra={"provider": self.session.name, "duration": f"{duration:.2f}s"})
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
            logger.error(f"ManagedPage: Error during shielded release: {e}")

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
        
        # Background Tasks Tracking
        self.autosave_task: Optional[asyncio.Task] = None
        self.eviction_task: Optional[asyncio.Task] = None
        self._orphan_cleanup_tasks = weakref.WeakSet()
        self.active_orphans = weakref.WeakSet()
        
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
            "max_capacity": self.max_conversations,
            "total_pages": self.page_count
        }

    @property
    def page_count(self) -> int:
        """
        Calculates the total number of live browser pages owned by this session.
        Counted: IDLE, LEASED, INVALIDATING.
        Excluded: DEAD.
        """
        count = 0
        # 1. Registry tabs (IDLE or LEASED)
        for tab in self.conversation_registry.values():
            if tab.status != TabStatus.DEAD:
                count += 1
        
        # 2. Active Orphans (INVALIDATING)
        for tab in self.active_orphans:
            if tab.status != TabStatus.DEAD:
                count += 1
        
        # 3. Internal pages (keepalive)
        if self.keepalive_page and not self.keepalive_page.is_closed():
            count += 1
            
        return count

    async def get_eviction_candidates(self) -> List[PersistentTab]:
        """
        Returns a prioritized list of tabs eligible for best-effort eviction.
        Priority: 
        1. INVALIDATING (Orphans)
        2. IDLE (Persistent LRU)
        3. STALE LEASED (Unresponsive)
        """
        candidates = []
        now = time.monotonic()
        
        async with self.registry_lock:
            # 1. Orphans (already in INVALIDATING)
            for tab in list(self.active_orphans):
                if tab.status == TabStatus.INVALIDATING:
                    candidates.append(tab)
            
            # 2. IDLE tabs (LRU: ordered by insertion/move_to_end)
            for tab in self.conversation_registry.values():
                if tab.status == TabStatus.IDLE:
                    candidates.append(tab)
            
            # 3. STALE LEASED tabs
            for tab in self.conversation_registry.values():
                if tab.status == TabStatus.LEASED:
                    if now - tab.last_heartbeat_at > self.lease_timeout:
                        candidates.append(tab)
                        
        return candidates

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
                            else:
                                # Detached recovery for orphaned leased tab
                                self._schedule_orphan_cleanup(stale_tab)
                            
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
            await self.engine.enforce_soft_cap()
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
                else:
                    # Detached recovery for orphaned leased tab
                    self._schedule_orphan_cleanup(old_tab)
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
                    else:
                        # Detached recovery for orphaned leased tab
                        self._schedule_orphan_cleanup(t)
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
                else:
                    # Detached recovery for orphaned leased tab
                    self._schedule_orphan_cleanup(tab)
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
                now = time.monotonic()
                to_evict = []
                stale_recovery = []
                
                async with self.registry_lock:
                    for cid, tab in list(self.conversation_registry.items()):
                        # 1. Idle Eviction (Uses accessed_at)
                        if tab.status == TabStatus.IDLE and (now - tab.last_accessed_at > self.idle_timeout):
                            to_evict.append(cid)
                        
                        # 2. Stale Lease Recovery (Crashed Request protection)
                        # Uses heartbeat_at to avoid killing healthy active streams
                        if tab.status == TabStatus.LEASED and (now - tab.last_heartbeat_at > self.lease_timeout):
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
                        logger.info(f"Idle eviction: {cid}", extra={"provider": self.name})

                for cid in stale_recovery:
                    tab_to_kill = None
                    async with self.registry_lock:
                        tab = self.conversation_registry.get(cid)
                        if tab and tab.status == TabStatus.LEASED:
                            logger.warning(f"Stale lease recovery: {cid} (No heartbeat for {self.lease_timeout}s)")
                            # ATOMIC INVALIDATION
                            tab_to_kill = self.conversation_registry.pop(cid, None)
                    
                    if tab_to_kill:
                        tab_to_kill.invalidate()
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
        """Teardown all session resources and track tasks."""
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

        # Drain orphan cleanup tasks
        if hasattr(self, "_orphan_cleanup_tasks"):
            orphan_tasks = list(self._orphan_cleanup_tasks)
            for task in orphan_tasks:
                if not task.done():
                    task.cancel()
            if orphan_tasks:
                await asyncio.gather(*orphan_tasks, return_exceptions=True)

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

    def _schedule_orphan_cleanup(self, tab: PersistentTab):
        """Schedules a detached physical close for a leased tab that was removed from registry."""
        if tab._cleanup_scheduled:
            return
        tab._cleanup_scheduled = True
        self.active_orphans.add(tab)

        async def _delayed_close():
            token_at_start = tab.lease_token
            try:
                while True:
                    # Check periodically based on lease_timeout
                    await asyncio.sleep(self.lease_timeout)
                    
                    # 0. Fast exit if tab already dead or ownership changed
                    if tab.status == TabStatus.DEAD:
                        return
                    if tab.lease_token != token_at_start:
                        return
                    
                    # 1. Heartbeat check: Protect healthy active streams
                    time_since_heartbeat = time.monotonic() - tab.last_heartbeat_at
                    if time_since_heartbeat < self.lease_timeout:
                        logger.debug(f"Orphan cleanup SKIP for {tab.conversation_id} (Heartbeat fresh: {time_since_heartbeat:.1f}s)")
                        continue
                    
                    # 2. TOCTOU Mitigation: Re-read heartbeat after a cooperative yield
                    # This ensures we don't kill a tab that just reported progress before our wakeup
                    latest_heartbeat = tab.last_heartbeat_at
                    await asyncio.sleep(0)
                    if latest_heartbeat != tab.last_heartbeat_at:
                        logger.debug(f"Orphan cleanup ABORTED for {tab.conversation_id} (Late heartbeat detected)")
                        continue

                    # 3. Deterministic Kill: Tab is orphaned AND unresponsive
                    if tab.lease_token == token_at_start and tab.status != TabStatus.DEAD:
                        logger.warning(f"Orphan cleanup KILL for {tab.conversation_id} (Stalled for {time_since_heartbeat:.1f}s)")
                        # Shield physical close from engine shutdown cancellation
                        await asyncio.shield(tab.close())
                    return
            except Exception as e:
                logger.error(f"Detached cleanup CRASHED for {tab.conversation_id}: {e}")
            finally:
                self.active_orphans.discard(tab)
                tab._cleanup_scheduled = False

        # Fire and forget detached cleanup task
        task = asyncio.create_task(_delayed_close())
        self._orphan_cleanup_tasks.add(task)

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
        self.max_total_tabs = CONFIG["Playwright"].getint("max_total_tabs", 50)
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
        async with self.management_lock:
            if self.is_shutting_down: return
            logger.info("BrowserEngine: Shutting down...")
            self.is_shutting_down = True
            
            drain_start = time.monotonic()
            drain_timeout = 15.0
            while self.active_pages > 0 and (time.monotonic() - drain_start) < drain_timeout:
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
