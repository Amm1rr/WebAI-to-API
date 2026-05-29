import asyncio
import uuid
import time
from enum import Enum
from typing import Optional, Any
from playwright.async_api import Page
from app.logger import logger

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
        
        # Internal task reference to deduplicate active cleanup tasks in an asyncio-safe manner
        self._cleanup_task: Optional[asyncio.Task] = None
        
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
        """
        Structural validation only. 
        Checks internal state, generation, and basic Playwright page status.
        Does NOT perform active liveness probes or rely on transport-level connectivity.
        Authority for active liveness resides in acquire_lease() and _reaper_loop().
        """
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
        
        try:
            # 1. Basic Re-validation (State & Generation)
            if self.status != TabStatus.IDLE or self.page.is_closed():
                return None

            # 2. Active Liveness Probe (Lightweight)
            # This detects transport disconnects where is_closed() might be stale.
            try:
                await asyncio.wait_for(self.page.evaluate("1"), timeout=1.0)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(
                    f"Tab({self.conversation_id}) liveness probe failed: {e}",
                    extra={
                        "generation": self.browser_generation,
                        "tab_id": self.conversation_id,
                        "req_id": request_id
                    }
                )
                self.invalidate()
                return None
                
            token = str(uuid.uuid4())
            self.status = TabStatus.LEASED
            self.lease_token = token
            self.owner_request_id = request_id
            self.leased_at = time.monotonic()
            
            # Initialize both timestamps for the new lease
            self.last_accessed_at = self.leased_at
            self.heartbeat("initial_lease")
            
            logger.info(f"Tab({self.conversation_id}): Lease acquired", extra={"token": token, "req_id": request_id, "generation": self.browser_generation})
            return token
        except Exception as e:
            logger.error(
                f"Tab({self.conversation_id}): Unexpected error acquiring lease: {e}",
                exc_info=True,
                extra={"generation": self.browser_generation, "tab_id": self.conversation_id, "req_id": request_id}
            )
            return None
        finally:
            if not self.lease_token:
                self._lock.release()

    async def release_lease(self, token: str) -> bool:
        """
        Releases the lease if the token matches.
        Returns True if released, False if token mismatch or already released.
        """
        if not self._lock.locked() or self.lease_token != token:
            logger.warning(f"Tab({self.conversation_id}): Rejected lease release attempt (Token Mismatch)", extra={"generation": self.browser_generation, "tab_id": self.conversation_id, "token": token})
            return False

        try:
            # Only return to IDLE if we aren't being killed/invalidated
            if self.status == TabStatus.LEASED:
                self.status = TabStatus.IDLE
            
            self.lease_token = None
            self.owner_request_id = None
            self.leased_at = None
            self.last_accessed_at = time.monotonic() # Final access update
            logger.info(f"Tab({self.conversation_id}): Lease released", extra={"token": token, "generation": self.browser_generation, "tab_id": self.conversation_id})
            return True
        finally:
            self._lock.release()

    def invalidate(self):
        """Transitions the tab to an invalid state, preventing further leases."""
        if self.status not in (TabStatus.INVALIDATING, TabStatus.DEAD):
            self.status = TabStatus.INVALIDATING

    async def close(self):
        """Safely shuts down the tab and marks it as dead."""
        async with self._lock:
            if self.status == TabStatus.DEAD:
                return
            
            self.status = TabStatus.INVALIDATING
            try:
                if not self.page.is_closed():
                    await self.page.close()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug(
                    f"Tab({self.conversation_id}): Best-effort page close failed: {e}",
                    extra={"generation": self.browser_generation, "tab_id": self.conversation_id}
                )
            finally:
                self.status = TabStatus.DEAD
                logger.debug(f"Tab({self.conversation_id}): Status set to DEAD", extra={"generation": self.browser_generation, "tab_id": self.conversation_id})

class ManagedPage:
    """
    A request-scoped lease on a browser page.
    Owns exactly one semaphore permit and potentially one PersistentTab lease.
    """
    def __init__(self, page: Page, session: Any, 
                 persistent_tab: Optional[PersistentTab] = None,
                 lease_token: Optional[str] = None,
                 request_id: str = "default"):
        self.page = page
        self.session = session
        self.persistent_tab = persistent_tab
        self.lease_token = lease_token
        self.request_id = request_id
        self._released = False
        self._lock = asyncio.Lock()
        self.acquired_at = time.monotonic()

    async def close(self):
        """
        Idempotent release of the lease. 
        Ensures semaphore is returned and persistent tab is unlocked.
        FIX: Uses asyncio.shield to prevent lease/lock leak during cancellation.
        """
        await asyncio.shield(self._do_close_safely())

    async def _do_close_safely(self):
        async with self._lock:
            if self._released:
                return
            self._released = True
            await self._do_close()

    async def _do_close(self):
        """Actual release implementation, shielded from cancellation.

        Safety: Cleanup order must always be:
        1. Release conversation ownership and tab/page resources (lease release).
        2. Release session semaphore permit (semaphore release).
        """
        try:
            # 0. Active Conversation Ownership Release with Stale-Finalizer Protection (Zero-Await)
            if self.persistent_tab:
                cid = self.persistent_tab.conversation_id
                async def safe_ownership_release():
                    async with self.session.conversation_lock:
                        # Conditional Release & Stale-Finalizer Protection
                        if self.session.active_conversations.get(cid) == self.request_id:
                            self.session.active_conversations.pop(cid, None)
                try:
                    await safe_ownership_release()
                except Exception as ownership_err:
                    logger.error(
                        f"Failed to release ownership for conversation {cid}: {ownership_err}",
                        extra={"generation": self.session.engine.browser_generation}
                    )

            # 1. Lifecycle management (lease release)
            if self.persistent_tab and self.lease_token:
                # Return to registry via the tab's own release API
                success = await self.persistent_tab.release_lease(self.lease_token)
                if success:
                    # If tab was invalidated/killed while leased, ensure physical close now
                    if self.persistent_tab.status in (TabStatus.INVALIDATING, TabStatus.DEAD):
                        await self.persistent_tab.close()
                    
                    duration = time.monotonic() - self.acquired_at
                    logger.info(f"PersistentTab({self.persistent_tab.conversation_id}): Lease released.", 
                                extra={
                                    "provider": self.session.name, 
                                    "duration": f"{duration:.2f}s",
                                    "tab_id": self.persistent_tab.conversation_id,
                                    "generation": self.session.engine.browser_generation
                                })
                else:
                    logger.warning(
                        f"PersistentTab({self.persistent_tab.conversation_id}): Ownership lost or already released.",
                        extra={
                            "provider": self.session.name,
                            "tab_id": self.persistent_tab.conversation_id,
                            "generation": self.session.engine.browser_generation
                        }
                    )
            else:
                # One-off temporary tab: physical close
                try:
                    if not self.page.is_closed():
                        await self.page.close()
                    logger.info("Temporary tab closed.", extra={"provider": self.session.name, "generation": self.session.engine.browser_generation})
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.debug(
                        f"ManagedPage: Best-effort temporary page close failed: {e}",
                        extra={"provider": self.session.name, "generation": self.session.engine.browser_generation}
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(
                f"ManagedPage: Error during shielded release: {e}",
                exc_info=True,
                extra={
                    "provider": self.session.name,
                    "generation": self.session.engine.browser_generation,
                    "tab_id": self.persistent_tab.conversation_id if self.persistent_tab else "temporary"
                }
            )
        finally:
            # 2. Semaphore return (semaphore release) - must always run even if resource release throws!
            self.session.semaphore.release()
            self.session.active_lease_count = max(0, self.session.active_lease_count - 1)
