import asyncio
import json
import inspect
import os
import time
import weakref
from collections import OrderedDict
from typing import Optional, Dict, Any, List, TYPE_CHECKING, Callable
from playwright.async_api import Error as PlaywrightError, Page, BrowserContext

from app.logger import logger
from app.config import CONFIG, get_default_auth_state_dir
from app.services.browser.tab import TabStatus, PersistentTab, ManagedPage
from app.services.browser.errors import BrowserDisconnectedError, BrowserShuttingDownError, LeaseInvalidatedError, SessionNotAliveError, ConversationBusyError

if TYPE_CHECKING:
    from app.services.browser.engine import BrowserEngine

class ProviderSession:
    """
    Manages isolated browser resources for a specific provider.
    Handles persistent tab registry and deterministic leasing.
    """
    def __init__(self, engine: 'BrowserEngine', name: str, enable_persistence: bool = False):
        self.engine = engine
        self.name = name
        self.enable_persistence = enable_persistence
        self.context: Optional[BrowserContext] = None
        self.keepalive_page: Optional[Page] = None
        self.last_browser_generation: Optional[int] = None
        
        # Concurrency & Lifecycle
        self.max_pages = engine.max_pages
        self.semaphore = asyncio.Semaphore(self.max_pages)
        self.active_lease_count = 0
        
        self.init_lock = asyncio.Lock()   # For setup/re-init
        self._cleanup_lock = asyncio.Lock()  # Serializes session resource cleanup.
        self.registry_lock = asyncio.Lock() # ONLY for trivial registry mutations
        self.conversation_lock = asyncio.Lock() # Protects ONLY active_conversations; MUST NOT be held simultaneously with registry_lock
        self.state_lock = asyncio.Lock()  # For disk I/O
        self.submit_lock = asyncio.Lock() # For serializing prompt submission
        self._recovery_task: Optional[asyncio.Task] = None
        self.active_conversations: Dict[str, str] = {} # Maps conversation_id -> request_id (active ownership tracking)
        
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
        self._intentional_context_closes = weakref.WeakValueDictionary()
        self._active_request_handles: Dict[str, tuple[Callable[[BaseException], None], Callable[[], None]]] = {}
        self._lifecycle_tasks: set[asyncio.Task] = set()
        
        # Persistent state
        auth_state_dir = CONFIG["Playwright"].get("auth_state_dir", get_default_auth_state_dir())
        if auth_state_dir:
            os.makedirs(auth_state_dir, exist_ok=True)
        self.state_path = os.path.join(auth_state_dir, f"{name}.json")

    @property
    def is_alive(self) -> bool:
        """
        Structural session validity.
        Rely on ensure_healthy() for active liveness probing.
        """
        return (
            self.context is not None and 
            self.engine.browser is not None and 
            self.engine.runtime.is_browser_connected(self.engine.browser) and
            self.keepalive_page is not None and
            not self.keepalive_page.is_closed() and
            self.last_browser_generation == self.engine.browser_generation
        )

    @property
    def application_shutdown_requested(self) -> bool:
        return getattr(self.engine, "shutdown_requested", False) is True

    @property
    def shutdown_in_progress(self) -> bool:
        return (
            self.application_shutdown_requested
            or getattr(self.engine, "is_shutting_down", False) is True
        )

    def register_request_abort(
        self,
        request_id: str,
        signal_terminal: Callable[[BaseException], None],
        abort: Callable[[], None],
    ) -> None:
        self._active_request_handles[request_id] = (signal_terminal, abort)

    def unregister_request_abort(self, request_id: str) -> None:
        self._active_request_handles.pop(request_id, None)

    def signal_active_requests(self, error_factory: Callable[[], BaseException]) -> None:
        for signal_terminal, _ in tuple(self._active_request_handles.values()):
            signal_terminal(error_factory())

    def abort_active_requests(self) -> None:
        for _, abort in tuple(self._active_request_handles.values()):
            abort()

    def _track_lifecycle_task(self, task: asyncio.Task, source: str) -> None:
        if not isinstance(task, asyncio.Future):
            return
        self._lifecycle_tasks.add(task)

        def consume_result(done_task: asyncio.Task) -> None:
            self._lifecycle_tasks.discard(done_task)
            if done_task.cancelled():
                return
            try:
                error = done_task.exception()
            except Exception as inspection_error:
                logger.error(
                    "ProviderSession(%s): Failed to inspect %s task: %s",
                    self.name,
                    source,
                    inspection_error,
                    exc_info=True,
                )
                return
            if error is not None:
                logger.error(
                    "ProviderSession(%s): %s task failed: %s",
                    self.name,
                    source,
                    error,
                    exc_info=(type(error), error, error.__traceback__),
                    extra={"generation": self.last_browser_generation},
                )

        task.add_done_callback(consume_result)

    async def _drain_lifecycle_tasks(self) -> None:
        current = asyncio.current_task()
        tasks = [task for task in self._lifecycle_tasks if task is not current and not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _drain_orphan_cleanup_tasks(self) -> None:
        tasks = list(self._orphan_cleanup_tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            self._orphan_cleanup_tasks.difference_update(tasks)
            for tab in list(self.active_orphans):
                if getattr(tab, "_cleanup_task", None) in tasks:
                    tab._cleanup_task = None
                    self.active_orphans.discard(tab)

    async def _cancel_pending_recovery(self) -> None:
        task = self._recovery_task
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._recovery_task = None

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
        if self.engine.is_shutting_down or self.application_shutdown_requested:
            raise BrowserShuttingDownError("BrowserEngine is shutting down")

        # 1. Atomic Check-and-Reserve under conversation_lock (Strictly Zero-Await for lookups/mutations)
        if conversation_id:
            async with self.conversation_lock:
                if conversation_id in self.active_conversations:
                    raise ConversationBusyError(f"Conversation {conversation_id} is busy with another active request.")
                self.active_conversations[conversation_id] = request_id

        # 2. Bound total request concurrency
        acquired_semaphore = False
        try:
            await self.semaphore.acquire()
            acquired_semaphore = True
            self.active_lease_count += 1
            
            await self.ensure_healthy()
            
            # 3. Conversational Reuse Flow
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
                            return ManagedPage(tab.page, self, persistent_tab=tab, lease_token=token, request_id=request_id, reserved_conversation_id=conversation_id)
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

            # 4. New Tab Flow
            await self.engine.enforce_soft_cap()
            page = await asyncio.wait_for(self.context.new_page(), timeout=10.0)
            await self._setup_page_bridge(page)
            return ManagedPage(page, self, request_id=request_id, reserved_conversation_id=conversation_id)

        except BaseException as e:
            if acquired_semaphore:
                self.active_lease_count = max(0, self.active_lease_count - 1)
                self.semaphore.release()
            
            # Guarded rollback of conversation ownership reservation (Shielded against cancellation)
            if conversation_id:
                async def safe_rollback():
                    async with self.conversation_lock:
                        if self.active_conversations.get(conversation_id) == request_id:
                            self.active_conversations.pop(conversation_id, None)
                try:
                    await asyncio.shield(safe_rollback())
                except Exception as rollback_err:
                    logger.error(
                        f"Failed to rollback ownership reservation for conversation {conversation_id}: {rollback_err}",
                        extra={"generation": self.engine.browser_generation}
                    )
            
            logger.debug(
                f"ProviderSession({self.name}): Aborted lease acquisition: {e}",
                extra={"generation": self.engine.browser_generation}
            )
            raise

    async def register_conversation(self, conversation_id: str, lease: ManagedPage) -> PersistentTab:
        """
        Promotes a temporary lease to a persistent conversation.
        Decoupled from registry_lock to avoid deadlocks.
        """
        # 1. Registration Generation Consistency Check
        from app.services.browser.errors import BrowserGenerationMismatchError, ConversationBusyError
        BrowserGenerationMismatchError.validate(
            self.last_browser_generation,
            self.engine.browser_generation,
            "Browser generation mismatch during conversation promotion."
        )

        # 2. Atomic Ownership Registration under conversation_lock (Strictly Zero-Await)
        async with self.conversation_lock:
            if conversation_id in self.active_conversations:
                if self.active_conversations[conversation_id] != lease.request_id:
                    raise ConversationBusyError(f"Conversation {conversation_id} is busy with another active request.")
            else:
                self.active_conversations[conversation_id] = lease.request_id

        # 3. Registry Promotion and Insertion
        try:
            # Note: A new tab is already exclusively 'owned' by the caller
            tab = PersistentTab(lease.page, conversation_id, self.engine.browser_generation)
            
            # 1. Pre-lock the tab before putting it in registry (Ownership Transfer)
            token = await tab.acquire_lease("internal_registration")
            if not token:
                raise LeaseInvalidatedError("Failed to lock new tab for registration")
            
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

        except BaseException as e:
            # Transactional Rollback: Remove active conversation registration if registry insertion fails
            async def safe_rollback():
                async with self.conversation_lock:
                    if self.active_conversations.get(conversation_id) == lease.request_id:
                        self.active_conversations.pop(conversation_id, None)
            try:
                await asyncio.shield(safe_rollback())
            except Exception as rollback_err:
                logger.error(
                    f"Failed to rollback ownership reservation on failed registration for conversation {conversation_id}: {rollback_err}",
                    extra={"generation": self.engine.browser_generation}
                )
            raise

    async def ensure_healthy(self):
        """Self-healing: Ensures browser process and provider context are functional."""
        if self.engine.is_shutting_down or self.application_shutdown_requested:
            raise BrowserShuttingDownError("Browser engine is shutting down")
            
        async with self.engine.management_lock:
            async with self.init_lock:
                await self.engine._ensure_healthy_browser()

                if self.engine.is_shutting_down or self.application_shutdown_requested:
                    raise BrowserShuttingDownError("Browser engine is shutting down")

                # Atomic Purge on generation rollover
                if self.last_browser_generation is not None and self.last_browser_generation != self.engine.browser_generation:
                    old_generation = self.last_browser_generation
                    new_generation = self.engine.browser_generation
                    logger.warning(
                        "ProviderSession(%s): Browser generation rollover (%s -> %s)",
                        self.name,
                        old_generation,
                        new_generation,
                        extra={
                            "provider": self.name,
                            "old_generation": old_generation,
                            "new_generation": new_generation,
                            "registry_size": len(self.conversation_registry)
                        }
                    )
                    await self._purge_all_tabs()

                # 2. Check active liveness of the session-wide keepalive page
                if not self.is_alive:
                    await self._setup_locked()
                else:
                    try:
                        if not self.keepalive_page or self.keepalive_page.is_closed():
                            await self._setup_locked()
                        else:
                            # ACTIVE PROBE: The authority for session liveness
                            await asyncio.wait_for(self.keepalive_page.evaluate("1"), timeout=2.0)
                    except Exception as e:
                        logger.warning(
                            f"ProviderSession({self.name}) liveness probe failed: {e}. Re-initializing.",
                            extra={"generation": self.last_browser_generation}
                        )
                        await self._setup_locked()

    async def _purge_all_tabs(self):
        """Atomically clears and invalidates all tabs in registry."""
        to_close = []
        idle_count = 0
        leased_count = 0

        async with self.registry_lock:
            initial_count = len(self.conversation_registry)
            for cid in list(self.conversation_registry.keys()):
                tab = self.conversation_registry.pop(cid)
                # Check status BEFORE invalidating
                if tab.status == TabStatus.IDLE:
                    to_close.append(tab)
                    idle_count += 1
                else:
                    # Detached recovery for orphaned leased tab
                    leased_count += 1
                    self._schedule_orphan_cleanup(tab)
                tab.invalidate()

        for t in to_close:
            await t.close()

        logger.info(
            "ProviderSession(%s): All tabs purged from registry",
            self.name,
            extra={
                "provider": self.name,
                "total_tabs": initial_count,
                "idle_tabs": idle_count,
                "leased_tabs": leased_count,
                "orphan_cleanup_scheduled": leased_count
            }
        )

    async def handle_session_failure(self):
        """Authoritative recovery execution for session failures."""
        if self.shutdown_in_progress:
            logger.debug(
                "ProviderSession(%s): Recovery skipped during shutdown.",
                self.name,
                extra={"generation": self.last_browser_generation},
            )
            return
        if self._recovery_task and not self._recovery_task.done():
            logger.debug(f"ProviderSession({self.name}): Recovery task already in progress, skipping duplicate request.")
            return

        # Synchronously create the recovery task (100% atomic in single-threaded event loop)
        self._recovery_task = asyncio.create_task(self._do_session_recovery())
        self._track_lifecycle_task(self._recovery_task, "recovery")

    async def _do_session_recovery(self):
        recovery_start = time.monotonic()
        async with self.init_lock:
            if self.shutdown_in_progress:
                logger.debug(
                    "ProviderSession(%s): Recovery worker exited during shutdown.",
                    self.name,
                    extra={"generation": self.last_browser_generation},
                )
                return
            logger.warning(
                "ProviderSession(%s): Session recovery started",
                self.name,
                extra={"provider": self.name, "generation": self.engine.browser_generation}
            )
            # 1. Intentionally preserve the persistent auth state file.
            # We do NOT delete the storage state file during recovery because transient failures
            # (such as temporary network issues or page load delays) should not destroy persisted sessions.
            if os.path.exists(self.state_path):
                logger.warning(
                    "ProviderSession(%s): Persistent auth state file exists "
                    "and is intentionally preserved during recovery to survive transient failure events.",
                    self.name,
                    extra={"auth_state_present": True}
                )

            # 2. Invalidate context to force re-setup on next ensure_healthy()
            try:
                if self.shutdown_in_progress:
                    logger.debug(
                        "ProviderSession(%s): Recovery cleanup skipped during shutdown.",
                        self.name,
                        extra={"generation": self.last_browser_generation},
                    )
                    return
                await self.close_resources(save_state=False)
                recovery_duration = time.monotonic() - recovery_start
                logger.info(
                    "ProviderSession(%s): Session recovery completed",
                    self.name,
                    extra={"provider": self.name, "duration_seconds": round(recovery_duration, 3), "generation": self.engine.browser_generation}
                )
            except Exception as e:
                recovery_duration = time.monotonic() - recovery_start
                logger.error(
                    "ProviderSession(%s): Session recovery failed: %s",
                    self.name,
                    e,
                    exc_info=True,
                    extra={"provider": self.name, "duration_seconds": round(recovery_duration, 3), "error": str(e)}
                )
                raise

    def _has_resources_to_close(self) -> bool:
        return any(
            (
                self.context is not None,
                self.keepalive_page is not None,
                bool(self.conversation_registry),
                bool(self.active_orphans),
                self.autosave_task is not None,
                self.eviction_task is not None,
                self.reaper_task is not None,
                bool(self._orphan_cleanup_tasks),
                self._cleanup_lock.locked(),
            )
        )

    async def _setup(self):
        async with self.engine.management_lock:
            async with self.init_lock:
                await self._setup_locked()

    async def _setup_locked(self):
        """Full re-initialization of the provider context."""
        if self._has_resources_to_close():
            await self.close_resources(save_state=False)

        context_args = {
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }

        from app.services.browser.auth_manager import get_auth_manager

        auth_strategy = get_auth_manager().get_strategy(self.name)
        if auth_strategy and hasattr(auth_strategy, "build_playwright_context_args"):
            strategy_context_args = auth_strategy.build_playwright_context_args(
                enable_persistence=self.enable_persistence,
                is_bootstrap=getattr(self.engine, "is_bootstrap", False),
            )
            if strategy_context_args:
                context_args.update(strategy_context_args)
        elif self._validate_state_file():
            context_args["storage_state"] = self.state_path

        try:
            self.context = await self.engine.browser.new_context(**context_args)
            context_generation = self.engine.browser_generation
            
            def safe_on_context_close(c):
                try:
                    task = asyncio.get_running_loop().create_task(
                        self._on_context_closed(c, context_generation)
                    )
                    if task is not None:
                        self._track_lifecycle_task(task, "context-close")
                except RuntimeError as e:
                    logger.debug(
                        "ProviderSession(%s): Context close callback scheduling skipped - event loop already closed: %s",
                        self.name,
                        e,
                        extra={"generation": self.last_browser_generation}
                    )
            self.context.on("close", safe_on_context_close)
            
            self.keepalive_page = await self.context.new_page()
            self.last_browser_generation = self.engine.browser_generation
            
            # Start background tasks
            if not self.engine.is_shutting_down and not self.application_shutdown_requested:
                # Background persistence tasks are strictly disabled in the runtime API service.
                enable_autosave = (
                    self.enable_persistence and 
                    os.environ.get("ENABLE_AUTOSAVE", "false").lower() == "true"
                )
                if enable_autosave and (not self.autosave_task or self.autosave_task.done()):
                    self.autosave_task = asyncio.create_task(self._autosave_loop())
                if not self.eviction_task or self.eviction_task.done():
                    self.eviction_task = asyncio.create_task(self._eviction_loop())
                if not self.reaper_task or self.reaper_task.done():
                    self.reaper_task = asyncio.create_task(self._reaper_loop())
            
            logger.info("provider_session_initialized", extra={"provider": self.name, "generation": self.last_browser_generation})
        except Exception as e:
            logger.error(f"ProviderSession({self.name}): Setup failed: {e}", exc_info=True, extra={"generation": self.engine.browser_generation})
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
            while not self.engine.is_shutting_down and not self.application_shutdown_requested:
                await asyncio.sleep(60)
                if self.is_alive:
                    await self.save_state()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            from app.services.browser.errors import WebAIRuntimeError
            if isinstance(e, WebAIRuntimeError):
                logger.error(
                    f"ProviderSession({self.name}): Autosave loop encountered semantic error: {type(e).__name__} - {e}. Escalating to recovery.",
                    exc_info=True,
                    extra={"generation": self.last_browser_generation}
                )
                task = asyncio.create_task(self.handle_session_failure())
                self._track_lifecycle_task(task, "recovery-request")
                raise
            else:
                logger.error(
                    f"ProviderSession({self.name}): Autosave loop error: {e}",
                    exc_info=True,
                    extra={"generation": self.last_browser_generation}
                )

    async def _eviction_loop(self):
        """Deterministic eviction and stale lease recovery."""
        try:
            while not self.engine.is_shutting_down and not self.application_shutdown_requested:
                await asyncio.sleep(30)
                if self.engine.is_shutting_down or self.application_shutdown_requested: break
                
                # Ignore stale generation state and delegate recovery/teardown to rollover purge flow
                if self.last_browser_generation != self.engine.browser_generation:
                    logger.debug(
                        "ProviderSession(%s): Generation mismatch detected (%s vs %s). Skipping eviction sweep.",
                        self.name, self.last_browser_generation, self.engine.browser_generation
                    )
                    continue
                
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
                        logger.info(f"Idle eviction: {cid}", extra={"provider": self.name, "generation": self.last_browser_generation, "tab_id": cid})

                for cid in stale_recovery:
                    tab_to_kill = None
                    async with self.registry_lock:
                        tab = self.conversation_registry.get(cid)
                        if tab and tab.status == TabStatus.LEASED:
                            logger.warning(
                                f"Stale lease recovery: {cid} (No heartbeat for {self.lease_timeout}s)",
                                extra={"provider": self.name, "generation": self.last_browser_generation, "tab_id": cid}
                            )
                            # ATOMIC INVALIDATION
                            tab_to_kill = self.conversation_registry.pop(cid, None)

                    if tab_to_kill:
                        tab_to_kill.invalidate()
                        await tab_to_kill.close()

        except asyncio.CancelledError:
            raise
        except Exception as e:
            from app.services.browser.errors import WebAIRuntimeError
            if isinstance(e, WebAIRuntimeError):
                logger.error(
                    f"ProviderSession({self.name}): Eviction loop encountered semantic error: {type(e).__name__} - {e}. Escalating to recovery.",
                    exc_info=True,
                    extra={"generation": self.last_browser_generation}
                )
                task = asyncio.create_task(self.handle_session_failure())
                self._track_lifecycle_task(task, "recovery-request")
                raise
            else:
                logger.error(
                    f"ProviderSession({self.name}): Eviction loop crashed: {e}",
                    exc_info=True,
                    extra={"generation": self.last_browser_generation}
                )

    async def _reaper_loop(self):
        """Active liveness sweeper for IDLE persistent tabs."""
        try:
            while not self.engine.is_shutting_down and not self.application_shutdown_requested:
                await asyncio.sleep(30)
                if self.engine.is_shutting_down or self.application_shutdown_requested: break
                
                # Ignore stale generation state and delegate liveness monitoring/recovery to authoritative flow
                if self.last_browser_generation != self.engine.browser_generation:
                    logger.debug(
                        "ProviderSession(%s): Generation mismatch detected (%s vs %s). Skipping reaper sweep.",
                        self.name, self.last_browser_generation, self.engine.browser_generation
                    )
                    continue

                if not self.is_alive:
                    if not self.engine.is_shutting_down and not self.application_shutdown_requested:
                        logger.warning(
                            "ProviderSession(%s): Unexpected liveness loss (Window closure). Triggering shutdown.",
                            self.name,
                            extra={"generation": self.last_browser_generation}
                        )
                        self.engine._on_browser_disconnected()
                    break

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
                        except Exception as e:
                            logger.debug(
                                f"ProviderSession({self.name}): Reaper liveness probe failed for tab {t.conversation_id}: {e}",
                                extra={"generation": self.last_browser_generation, "tab_id": t.conversation_id}
                            )

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
                        logger.info(
                            f"Reaper purged {len(to_close)} dead tabs from registry ({self.name})",
                            extra={"generation": self.last_browser_generation}
                        )

        except asyncio.CancelledError:
            raise
        except Exception as e:
            from app.services.browser.errors import WebAIRuntimeError
            if isinstance(e, WebAIRuntimeError):
                logger.error(
                    f"ProviderSession({self.name}): Reaper loop encountered semantic error: {type(e).__name__} - {e}. Escalating to recovery.",
                    exc_info=True,
                    extra={"generation": self.last_browser_generation}
                )
                task = asyncio.create_task(self.handle_session_failure())
                self._track_lifecycle_task(task, "recovery-request")
                raise
            else:
                logger.error(
                    f"Reaper loop crashed ({self.name}): {e}",
                    exc_info=True,
                    extra={"generation": self.last_browser_generation}
                )

    def _mark_intentional_context_close(self, context: BrowserContext) -> None:
        self._intentional_context_closes[id(context)] = context

    def _consume_intentional_context_close(self, context: BrowserContext) -> bool:
        return self._intentional_context_closes.pop(id(context), None) is context

    async def _on_context_closed(self, context: BrowserContext, generation: int):
        """Handler for BrowserContext.on('close')."""
        if self.engine.is_shutting_down:
            if (
                not self._consume_intentional_context_close(context)
                and generation == self.engine.browser_generation
                and self.context is context
            ):
                self.signal_active_requests(
                    lambda: BrowserDisconnectedError(
                        "Browser context closed during active request"
                    )
                )
            return

        if self._consume_intentional_context_close(context):
            logger.debug(
                "ProviderSession(%s): Ignoring intentional browser context close.",
                self.name,
                extra={"generation": self.last_browser_generation}
            )
            return

        if self.application_shutdown_requested:
            self.signal_active_requests(
                lambda: BrowserDisconnectedError(
                    "Browser context closed during application shutdown"
                )
            )
            logger.info(
                "ProviderSession(%s): Ignoring context close during application shutdown.",
                self.name,
                extra={
                    "shutdown_source": self.engine.shutdown_source,
                    "generation": self.last_browser_generation,
                },
            )
            return

        if generation != self.engine.browser_generation or self.context is not context:
            logger.debug(
                "ProviderSession(%s): Ignoring stale browser context close.",
                self.name,
                extra={
                    "context_generation": generation,
                    "current_generation": self.engine.browser_generation,
                    "provider": self.name,
                },
            )
            return
        
        logger.warning(f"ProviderSession({self.name}): Context closed (Window manually closed or crash).")
        self.signal_active_requests(
            lambda: BrowserDisconnectedError("Browser context closed during active request")
        )
        # Delegate terminal shutdown to engine
        self.engine._on_browser_disconnected()

    async def save_state(self):
        if not self.is_alive: return
        # The runtime API service does not contain an active persistence execution path.
        if not self.enable_persistence:
            logger.warning(
                f"ProviderSession({self.name}): save_state called outside of manual bootstrap utility flow. "
                "Persistence is disabled during active runtime API service execution."
            )
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
                logger.warning(
                    f"ProviderSession({self.name}): Failed to save state: {e}",
                    extra={"generation": self.last_browser_generation}
                )
                if os.path.exists(tmp_path):
                    try: os.remove(tmp_path)
                    except OSError: pass

    async def close_resources(self, save_state: bool = True):
        async with self._cleanup_lock:
            await self._close_resources(save_state=save_state)

    async def _close_resources(self, save_state: bool = True):
        """Teardown all session resources and track tasks."""
        try:
            if getattr(self.engine, "is_shutting_down", False) is True:
                await self._cancel_pending_recovery()

            logger.info(
                f"ProviderSession({self.name}): Closing session resources...",
                extra={"generation": self.last_browser_generation}
            )
            if save_state: 
                await self.save_state()
    
            if self.autosave_task:
                logger.info(f"ProviderSession({self.name}): Cancelling autosave_task...")
                self.autosave_task.cancel()
                try: 
                    await self.autosave_task
                except asyncio.CancelledError: 
                    pass
                except Exception as e:
                    logger.warning(
                        f"ProviderSession({self.name}): Error awaiting autosave task: {e}",
                        exc_info=True,
                        extra={"generation": self.last_browser_generation}
                    )
                self.autosave_task = None
    
            if self.eviction_task:
                logger.info(f"ProviderSession({self.name}): Cancelling eviction_task...")
                self.eviction_task.cancel()
                try: 
                    await self.eviction_task
                except asyncio.CancelledError: 
                    pass
                except Exception as e:
                    logger.warning(
                        f"ProviderSession({self.name}): Error awaiting eviction task: {e}",
                        exc_info=True,
                        extra={"generation": self.last_browser_generation}
                    )
                self.eviction_task = None
    
            if self.reaper_task:
                logger.info(f"ProviderSession({self.name}): Cancelling reaper_task...")
                self.reaper_task.cancel()
                try: 
                    await self.reaper_task
                except asyncio.CancelledError: 
                    pass
                except Exception as e:
                    logger.warning(
                        f"ProviderSession({self.name}): Error awaiting reaper task: {e}",
                        exc_info=True,
                        extra={"generation": self.last_browser_generation}
                    )
                self.reaper_task = None
    
            await self._drain_orphan_cleanup_tasks()
            await self._purge_all_tabs()
            await self._drain_orphan_cleanup_tasks()
    
            if self.keepalive_page:
                try:
                    if not self.keepalive_page.is_closed():
                        logger.info(f"ProviderSession({self.name}): Closing keepalive page...")
                        await self.keepalive_page.close()
                except Exception as e:
                    logger.warning(
                        f"ProviderSession({self.name}): Failed to close keepalive page: {e}",
                        exc_info=True,
                        extra={"generation": self.last_browser_generation}
                    )
                self.keepalive_page = None
    
            if self.context:
                context_to_close = self.context
                self.context = None
                self._mark_intentional_context_close(context_to_close)
                try: 
                    if context_to_close.is_closed():
                        logger.debug(
                            f"ProviderSession({self.name}): Browser context already closed.",
                            extra={"generation": self.last_browser_generation},
                        )
                    else:
                        logger.info(f"ProviderSession({self.name}): Closing browser context...")
                        await context_to_close.close()
                except PlaywrightError as close_error:
                    try:
                        context_closed = context_to_close.is_closed()
                    except Exception as inspection_error:
                        self._consume_intentional_context_close(context_to_close)
                        logger.warning(
                            f"ProviderSession({self.name}): Failed to inspect browser context after close error "
                            f"{close_error}: {inspection_error}",
                            exc_info=(
                                type(close_error),
                                close_error,
                                close_error.__traceback__,
                            ),
                            extra={"generation": self.last_browser_generation},
                        )
                    else:
                        if context_closed:
                            logger.debug(
                                f"ProviderSession({self.name}): Browser context already closed during cleanup.",
                                extra={"generation": self.last_browser_generation},
                            )
                        else:
                            self._consume_intentional_context_close(context_to_close)
                            logger.warning(
                                f"ProviderSession({self.name}): Failed to close browser context: {close_error}",
                                exc_info=(
                                    type(close_error),
                                    close_error,
                                    close_error.__traceback__,
                                ),
                                extra={"generation": self.last_browser_generation},
                            )
                except Exception as e:
                    self._consume_intentional_context_close(context_to_close)
                    logger.warning(
                        f"ProviderSession({self.name}): Failed to close browser context: {e}",
                        exc_info=True,
                        extra={"generation": self.last_browser_generation}
                    )

            await asyncio.sleep(0)
            await self._drain_lifecycle_tasks()

            logger.info(
                f"ProviderSession({self.name}): Session resources closed successfully.",
                extra={"generation": self.last_browser_generation}
            )
        except Exception as e:
            logger.error(
                f"ProviderSession({self.name}): Session cleanup failed: {e}",
                exc_info=True,
                extra={"generation": self.last_browser_generation}
            )
            raise

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
        self._track_lifecycle_task(task, "orphan-cleanup")

    async def _setup_page_bridge(
        self,
        page: Page,
        binding_name: str = "__browser_bridge",
        callbacks_attr: str = "_browser_bridge_callbacks",
    ):
        """Expose a permanent request bridge binding on the page for a provider."""
        page_state = vars(page)
        exposed_bindings = page_state.get("_browser_bridge_exposed_bindings")
        if exposed_bindings is None:
            exposed_bindings = set()
            page._browser_bridge_exposed_bindings = exposed_bindings
        if binding_name in exposed_bindings:
            if callbacks_attr not in page_state:
                setattr(page, callbacks_attr, {})
            return

        # Lock to serialize bridge setup on the same page.
        lock = page_state.get("_browser_bridge_lock")
        if lock is None:
            lock = asyncio.Lock()
            page._browser_bridge_lock = lock

        async with lock:
            page_state = vars(page)
            exposed_bindings = page_state.get("_browser_bridge_exposed_bindings")
            if exposed_bindings is None:
                exposed_bindings = set()
                page._browser_bridge_exposed_bindings = exposed_bindings
            if binding_name in exposed_bindings:
                if callbacks_attr not in page_state:
                    setattr(page, callbacks_attr, {})
                return

            if callbacks_attr not in page_state:
                setattr(page, callbacks_attr, {})
            
            async def page_bridge(source, payload):
                req_id = payload.get("requestId")
                if not req_id:
                    logger.error("Bridge received payload without requestId", extra={"payload": payload})
                    return
                
                logger.debug(
                    f"Bridge payload received. requestId: {req_id}, type: {payload.get('type')}",
                    extra={"request_id": req_id, "payload_type": payload.get("type")}
                )
                
                callbacks = vars(page).get(callbacks_attr, {})
                callback = callbacks.get(req_id)
                if not callback:
                    logger.error(
                        f"Bridge callback lookup missed for requestId: {req_id}",
                        extra={
                            "request_id": req_id,
                            "payload": payload,
                            "exposed_callbacks": list(callbacks.keys())
                        }
                    )
                    return

                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(source, payload)
                    else:
                        callback(source, payload)
                except Exception as e:
                    logger.error(
                        f"Exception during bridge callback execution for requestId: {req_id}: {e}",
                        exc_info=True,
                        extra={"request_id": req_id}
                    )

            expose_result = page.expose_binding(binding_name, page_bridge)
            if inspect.isawaitable(expose_result):
                await expose_result
            exposed_bindings.add(binding_name)
