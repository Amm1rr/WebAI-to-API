# Concurrency Model

This document specifies the concurrency contracts, resource ownership, and synchronization primitives used in the Playwright runtime.

## 1. Resource Ownership & Lifecycle

### 1.1 Semaphore Ownership
- **Authority**: `ProviderSession` owns the request-scoped semaphore.
- **Contract**: Every active browser request must hold exactly one semaphore permit.
- **Leak Prevention**: Permits are released only via `ManagedPage.close()`.

### 1.2 ManagedPage Lifecycle
`ManagedPage` is a request-scoped container representing an active lease on a browser page.
- **Acquisition**: Created via `ProviderSession.acquire_lease()`.
- **Termination**: Must be explicitly closed using `await page_lease.close()`.
- **Invariants**:
    - Releasing a `ManagedPage` is idempotent.
    - Release logic must be wrapped in `asyncio.shield` to ensure permit return and lock release during request cancellation.

### 1.3 PersistentTab Leasing
- **Model**: `PersistentTab` objects are long-lived and reside in the `ProviderSession.conversation_registry`.
- **Lease Token**: Access is granted via a unique `lease_token`. Only the token holder may perform operations or release the tab.
- **Exclusivity**: Acquisition of a lease is mutually exclusive via `PersistentTab._lock`.

### 1.4 Single-Owner Mutation Invariant
- **Contract**: A leased `PersistentTab` may only be mutated by the active lease holder.
- **Background Protection**: Background loops MUST NOT mutate actively leased tabs except during terminal invalidation or engine shutdown.

### 1.5 Lease Invalidation Semantics
- **Boundary**: Lease ownership becomes invalid immediately when:
    - The underlying page crashes or is poisoned.
    - A browser generation rollover occurs.
    - Engine shutdown is initiated.
- **Enforcement**: Operations on invalid leases MUST fail fast.

## 2. Lock Hierarchy & Deadlock Prevention

Locks MUST be acquired in the following order. Acquiring out-of-order is strictly **forbidden** and results in deterministic deadlocks.

1. `BrowserEngine.management_lock`: Orchestrates global initialization and terminal shutdown.
2. `ProviderSession.init_lock`: Serializes session-specific browser context setup.
3. `ProviderSession.registry_lock`: Protects the in-memory `conversation_registry` and `active_orphans` set.
4. `PersistentTab._lock`: Protects individual tab state transitions and lease ownership.

### 2.1 Lock Scope Discipline
- **Invariant**: `registry_lock` MUST NEVER be held across Playwright operations, network waits, or long-running awaits.
- **Rule**: Locks must protect synchronous state mutation only and be released immediately. Violating this scope discipline risks global request starvation.

### 2.2 Recovery Concurrency Guarantees
- **Serialization**: `ProviderSession.init_lock` serializes all recovery and setup paths.
- **Convergence**: Concurrent recovery attempts must converge into a single authoritative execution path. Subsequent callers must wait and then verify the new state.

## 3. Cancellation Safety

- **Deterministic Ordering**: Cleanup ordering must remain deterministic even under `CancelledError`.
- **Atomic Releases**: Resource cleanup MUST be shielded using `asyncio.shield` to prevent task cancellation from causing leaks. This is mandatory for:
    - Semaphore permit release.
    - Lock release.
    - Callback registry cleanup.
    - Lease invalidation.
- **Orphan Cleanup**: Tabs that lose their owning request due to timeout or cancellation without a clean release are tracked as **Orphans** and reaped by a background task.

## 4. Background Synchronization

### 4.1 Periodic Loops
`ProviderSession` runs three decoupled background loops:
- **Reaper Loop**: Active liveness sweeper. Purges `DEAD` tabs and detects window closure.
- **Autosave Loop**: Periodically persists browser context state to disk.
- **Eviction Loop**: Enforces conversation capacity and recovers stalled leases.

### 4.2 Loop Authority Boundaries
- **Capabilities**: Loops may perform tab-level cleanup, invalidation bookkeeping, or stale lease recovery.
- **Restrictions**: Loops MUST NEVER recreate browser contexts or processes directly. 
- **Escalation**: Lifecycle escalation (recovery) belongs exclusively to `ProviderSession` and `BrowserEngine` authoritative paths.

### 4.3 Loop Invariants
- Loops must check `self.engine.is_shutting_down` at every iteration.
- Loops must use `is_alive` property to skip operations on disconnected browsers.
- Loop operations that mutate the registry MUST acquire `registry_lock`.

## 5. AI Agent Rules

AI Agents working on the concurrency or locking logic must adhere to these strict constraints:

1. **No Lock-Order Violations**: Never acquire locks out-of-order (Management -> Init -> Registry -> Tab).
2. **No Await under Registry Lock**: Never perform an `await` while holding `registry_lock`.
3. **Mandatory Shielding**: Always wrap resource cleanup in `asyncio.shield`.
4. **No Silent Reuse**: Never attempt to reuse a lease after it has been invalidated by crash or rollover.
5. **No Unmanaged Mutations**: Never mutate a leased tab from a background task unless it is a terminal shutdown.
6. **Fail-Fast Ownership**: Verify lease ownership before every lease-sensitive operation, tab mutation, or release.

## 6. Gemini Conversation Sessions

### 6.1 Per-Session Locking via SessionManager.lock
Each `SessionManager` utilizes an internal `asyncio.Lock` (`self.lock`) to serialize all stateful completion and streaming operations under the same `conversation_id`. Every request accessing a specific conversation must acquire this lock before mutating the session or sending messages.

### 6.2 Serialization of Requests Sharing the same conversation_id
Because stateful chats depend on maintaining sequential and consistent message history on the model provider backend, concurrent requests requesting the same `conversation_id` are strictly serialized. One request must fully complete (or its stream must fully close and release the lock) before the next request in line can acquire the lock and execute.

### 6.3 Asyncio/Session-Safety Constraints of Google's ChatSession
Concurrent requests must not execute simultaneously on the same Gemini `ChatSession`. The underlying Google client library's `ChatSession` maintains mutable internal state (history, sequence markers). Concurrent execution on the same session violates sequence ordering and can lead to internal state corruption, race conditions, or protocol failures on the Google backend.

### 6.4 Streaming Behavior and active_streams Tracking
Progressive streaming requests track their active count using `active_streams`.
* **State tracking**: When a stream request starts, it increments `self.active_streams` before acquiring `self.lock`.
* **Cleanup**: On stream closure, error, or client cancellation, the stream decrements `self.active_streams` inside a `finally` block.
This ensures that long-lived streaming tasks remain registered as active even during lock-wait states or slow-generation phases.

### 6.5 Session Pruning Interaction with active_streams
The passive pruning mechanism in `SessionRegistry` evaluates active streams to prevent premature eviction of active clients:
* **Active Stream Pinning**: A session manager is pinned in memory if `self.active_streams > 0` or if its lock is currently acquired (`self.lock.locked()`).
* **Prunability**: The registry is prohibited from evicting any session that is currently processing a request or has active stream generator references, avoiding dangling stream references and runtime errors.

### 6.6 Multi-Tab / Multi-Agent Limitation
The design enforces a strict 1-to-1 relationship between an active `conversation_id` and a single logical client. The system does not support a multi-tab or multi-agent paradigm where multiple independent actors concurrently interact with the exact same thread.

### 6.7 Branching Conversation Corruption Risk
If multiple clients concurrently reuse the same `conversation_id` and send differing messages:
* Because they are serialized, their messages will interleave sequentially rather than branching.
* There is a high risk of branching conversation corruption since there is no server-side history branching mechanism. The model will treat interleaving requests as one linear history, poisoning the context for all participating clients.

### 6.8 Asyncio Execution Assumptions
The lock safety and event-loop-safe guarantees of this concurrency model depend on the cooperative multitasking model of `asyncio`.
* Within the supported deployment model, SessionRegistry and SessionManager execute inside a single asyncio event loop per worker process.
* The atomic update of `active_streams` and lock acquisition relies on the fact that context switches only occur at explicit `await` boundaries.
* **Important**: This model assumes a single-event-loop execution environment. It is not designed or proven as a universal guarantee across arbitrary multi-threaded or multi-worker systems running without proper IPC/distributed locking.


