import os
import asyncio
import json
import time
import weakref
from collections import OrderedDict
from typing import Optional, Dict, Any, List, TYPE_CHECKING
from playwright.async_api import Page, BrowserContext

from app.logger import logger
from app.config import CONFIG
from app.services.browser.tab import TabStatus, PersistentTab, ManagedPage

if TYPE_CHECKING:
    from app.services.browser.engine import BrowserEngine

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
        self.submit_lock = asyncio.Lock() # For serializing prompt submission
        
        # Conversation Registry (LRU-capable)
        self.conversation_registry: Dict[str, PersistentTab] = OrderedDict()
        self.max_conversations = CONFIG["Playwright"].getint("max_persistent_conversations", 20)
        self.idle_timeout = CONFIG["Playwright"].getint("idle_conversation_timeout", 900) # 15 mins
        self.lease_timeout = CONFIG["Playwright"].getint("lease_timeout", 180) # 3 mins
        
        # Background Tasks Tracking
        self.autosave_task: Optional[asyncio.Task] = None
        self.eviction_task: Optional[asyncio.Task] = None
        self.reaper_task: Optional[asyncio.Task] = None
        self._orphan_cleanup_tasks = set()
        self.active_orphans = weakref.WeakSet()
        
        # Persistent state
        self.state_path = os.path.join(engine.user_data_dir, f"{name}_state.json")

    @property
    def is_alive(self) -> bool:
        """
        Structural session validity.
        Rely on ensure_healthy() for active liveness probing.
        """
        return (
            self.context is not None and 
            self.engine.browser is not None and 
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
                                if self.conversation_registry.get(conversation_id) is tab:
                                    self.conversation_registry.pop(conversation_id)
                            await tab.close()
                    else:
                        # Probe failed or tab became invalid/busy
                        if not tab.is_valid(self.engine.browser_generation):
                            # Purge if it's definitely dead/invalid (not just busy)
                            if tab.status != TabStatus.LEASED:
                                async with self.registry_lock:
                                    if self.conversation_registry.get(conversation_id) is tab:
                                        self.conversation_registry.pop(conversation_id)
                                await tab.close()
                        logger.debug(f"Tab {conversation_id} busy or invalid. Falling back to new tab.")

            # 3. New Tab Flow
            await self.engine.enforce_soft_cap()
            page = await asyncio.wait_for(self.context.new_page(), timeout=10.0)
            await self._setup_page_bridge(page)
            return ManagedPage(page, self)

        except BaseException:
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

            # 2. Check active liveness of the session-wide keepalive page
            if not self.is_alive:
                await self._setup()
            else:
                try:
                    if not self.keepalive_page or self.keepalive_page.is_closed():
                        await self._setup()
                    else:
                        # ACTIVE PROBE: The authority for session liveness
                        await asyncio.wait_for(self.keepalive_page.evaluate("1"), timeout=2.0)
                except (asyncio.TimeoutError, Exception):
                    logger.warning(f"ProviderSession({self.name}) liveness probe failed. Re-initializing.")
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
                if not self.reaper_task or self.reaper_task.done():
                    self.reaper_task = asyncio.create_task(self._reaper_loop())
            
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

    async def _reaper_loop(self):
        """Active liveness sweeper for IDLE persistent tabs."""
        try:
            while not self.engine.is_shutting_down:
                await asyncio.sleep(30)
                if not self.is_alive:
                    continue

                # 1. Snapshot IDLE tabs under registry_lock
                candidates = []
                async with self.registry_lock:
                    for tab in self.conversation_registry.values():
                        if tab.status == TabStatus.IDLE:
                            candidates.append(tab)

                # 2. Parallel Probe outside registry_lock (bounded concurrency)
                probe_sem = asyncio.Semaphore(10)

                async def probe_tab(t):
                    async with probe_sem:
                        try:
                            if (
                                not t.page.is_closed()
                                and t.browser_generation == self.engine.browser_generation
                            ):
                                # Active Liveness Probe (The real authority)
                                await asyncio.wait_for(
                                    t.page.evaluate("1"),
                                    timeout=1.0
                                )

                                return None

                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            pass

                        return t.conversation_id

                results = await asyncio.gather(
                    *(probe_tab(t) for t in candidates),
                    return_exceptions=False
                )

                dead_cids = [cid for cid in results if cid]

                # 3. Cleanup dead tabs under registry_lock
                if dead_cids:
                    to_close = []
                    async with self.registry_lock:
                        for cid in dead_cids:
                            tab = self.conversation_registry.get(cid)
                            # Only reap if still IDLE and hasn't been replaced
                            if tab and tab.status == TabStatus.IDLE:
                                t = self.conversation_registry.pop(cid)
                                to_close.append(t)
                                t.invalidate()

                    for t in to_close:
                        await t.close()
                    if to_close:
                        logger.info(f"Reaper purged {len(to_close)} dead tabs from registry ({self.name})")

        except asyncio.CancelledError: pass
        except Exception as e:
            logger.error(f"Reaper loop crashed ({self.name}): {e}")

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

        if self.reaper_task:
            self.reaper_task.cancel()
            try: await self.reaper_task
            except: pass
            self.reaper_task = None

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
        if getattr(tab, "_cleanup_task", None) and not tab._cleanup_task.done():
            return
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
                    await asyncio.sleep(0.01)  # Stiffer yield to let late writes and loop steps settle
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
                # Only clear if this task is still the registered cleanup task
                if getattr(tab, "_cleanup_task", None) is asyncio.current_task():
                    tab._cleanup_task = None
                self._orphan_cleanup_tasks.discard(asyncio.current_task())

        # Fire and forget detached cleanup task
        task = asyncio.create_task(_delayed_close())
        tab._cleanup_task = task
        self._orphan_cleanup_tasks.add(task)

    async def _setup_page_bridge(self, page: Page):
        """Exposes a single permanent binding on the page to prevent memory leaks."""
        if getattr(page, "_gemini_bridge_exposed", False):
            return
        
        # Lock to serialize bridge setup on the same page
        lock = getattr(page, "_gemini_bridge_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            page._gemini_bridge_lock = lock

        async with lock:
            if getattr(page, "_gemini_bridge_exposed", False):
                return
            
            page._gemini_callbacks = {}
            
            async def page_bridge(source, payload):
                req_id = payload.get("requestId")
                if not req_id:
                    return
                callback = getattr(page, "_gemini_callbacks", {}).get(req_id)
                if callback:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(source, payload)
                    else:
                        callback(source, payload)
                    
            await page.expose_binding("__gemini_bridge", page_bridge)
            page._gemini_bridge_exposed = True
