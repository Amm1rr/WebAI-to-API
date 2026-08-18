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

### 1.6 Browser Conversation Ownership

This contract governs `conversation_id` ownership for browser-native requests (e.g., Gemini Playwright). It is independent of the Gemini WebAPI `SessionManager.lock` semantics in [Section 6](#6-gemini-conversation-sessions).

- **Single Owner Invariant**: An active browser conversation has exactly one request owner at any time. The owner is recorded in the session's `active_conversations` map, guarded by `conversation_lock`.
- **Competing Registration**: A request that registers an already-owned active `conversation_id` is rejected with `ConversationBusyError`. Pre-header this maps to HTTP 409; post-header it terminates the stream internally (see [Error Policy](error-policy.md)).
- **Winner Preservation**: When a collision occurs, the current winner remains the owner. The winner's registry entry and tab are never mutated or displaced by the losing request.
- **Conditional Release**: Ownership release must be conditional on request identity. A cleanup path only removes the ownership entry if it still maps to its own request id; it must never unregister or mutate another request's ownership.
- **Losing-Request Cleanup**: A losing request's cleanup must not unregister the winner's ownership or alter the winner's `PersistentTab`/registry state.

## 2. Lock Hierarchy & Deadlock Prevention

Locks MUST be acquired in the following order. Acquiring out-of-order is strictly **forbidden** and results in deterministic deadlocks.

1. `BrowserEngine.management_lock`: Orchestrates global initialization and terminal shutdown.
2. `ProviderSession.init_lock`: Serializes session-specific browser context setup.
3. `ProviderSession._cleanup_lock`: Serializes ProviderSession resource cleanup.
4. `ProviderSession.registry_lock`: Protects the in-memory `conversation_registry` and `active_orphans` set.
5. `PersistentTab._lock`: Protects individual tab state transitions and lease ownership.

`conversation_lock` remains independent. No production path acquires `management_lock` from `init_lock`. `close_resources()` must not acquire `init_lock`; `_setup_locked()` assumes management and init locks are already held, `_setup()` acquires them in order, and `_ensure_healthy_browser()` assumes management-lock ownership.

### 2.1 Lock Scope Discipline
- **Invariant**: `registry_lock` MUST NEVER be held across Playwright operations, network waits, or long-running awaits.
- **Rule**: Locks must protect synchronous state mutation only and be released immediately. Violating this scope discipline risks global request starvation.

### 2.2 Recovery Concurrency Guarantees
- **Serialization**: `ProviderSession.init_lock` serializes all recovery and setup paths.
- **Convergence**: Concurrent recovery attempts must converge into a single authoritative execution path. Subsequent callers must wait and then verify the new state.

### 2.3 Shutdown Admission and Request Drain
- `shutdown_requested` closes new page/session admission before Uvicorn connection drain; `_shutdown_started` means terminal BrowserEngine cleanup has begun. First shutdown source wins.
- Existing requests may finish during the existing 15-second grace period. After the deadline, BrowserEngine invokes request-owned abort callbacks and waits for bounded cleanup.
- BrowserEngine never mutates lease counters. Request cleanup owns lease and semaphore release.
- Browser/page/context loss signals the request terminal event immediately, bypassing queue/chunk/total timeout waits.

### 2.4 Lifecycle Task Ownership
ProviderSession owns context-close callback, recovery, recovery-wrapper, and orphan-cleanup tasks. BrowserEngine owns disconnect-triggered close tasks. Project-owned task completion must retrieve/log exceptions; request observer and queue tasks remain request-owned and are explicitly awaited or cancelled.

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

This section applies to persistent conversations using `conversation_id`, primarily `/v1/chat/completions`. It does not apply to stateless temporary endpoints such as `/translate` and `/v1/temporary/chat/completions`.

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

### 6.7 Gemini Client Generation Authority and Leases
Only Gemini lifecycle initialization chooses generation IDs. Request, restore, registry, and streaming paths use explicit generations and MUST NOT allocate, increment, register, or repair them.

Generation selection, explicit registration, and publication occur under `_gemini_client_init_lock`. A replacement candidate remains private until the registry commit with its explicit generation succeeds. Request lease-counter mutations are synchronous; the registry lock protects registry state and publication checks, not generation allocation.

Gemini WebAPI request paths acquire a lease for the client generation they use. A replacement retires the previous generation but does not close it while its lease count is nonzero. Buffered requests release after upstream generation and response construction; stateful streaming requests release from the generator `finally` after completion, cancellation, timeout, or failure. Retired clients close when their final lease releases. No background retirement task is used.

New direct requests acquire the current client and generation atomically. Stateful requests acquire the manager's client-generation pair while holding `SessionManager.lock`; stale sessions remain subject to the existing lazy rebuild invariant.

For stateful or temporary streams, `GeminiLeaseStreamingResponse` owns cleanup of a transferred lease around the ASGI response call. Its cleanup covers response-start failure, disconnect, cancellation, normal completion, and a body iterator that never starts. Direct `/gemini` and Google Generative streams acquire their leases inside their generators after body execution begins and release them in generator cleanup.

### 6.8 SessionRegistry Generation Contract
`SessionRegistry` construction, `update_client()`, and `reopen()` require an explicitly supplied registered generation. The registry validates and propagates that generation to each `SessionManager`; it never allocates, increments, or repairs generation IDs. `SessionManager` requires explicit `client_generation`, while `session_generation` remains separate session state.

### 6.9 SessionRegistry Persistent Restore Coordination
Persistent snapshot restoration uses one in-flight restore producer per `conversation_id`. Same-ID followers await the shared task, shielded so cancellation of one waiter does not cancel the producer. Unrelated conversation IDs continue through the registry independently.

`SessionRegistry._lock` protects local registry checks, tombstones, pruning, and publication only. Snapshot I/O, validation, deserialization, and `client.start_chat()` run outside this lock. Before publication, the restore task rechecks deletion state, an existing session, the current client generation, and the capacity/pruning policy.

Restore captures `(client, generation)`, acquires an exact-generation lease, performs recovery outside the registry lock, and rechecks publication state. If lifecycle advanced, it releases the stale lease and performs a typed generation retry with the current pair. Restore never registers or repairs generation state.

Registry shutdown closes restore admission, cancels and awaits pending restore producers, and lets their generation leases release before Gemini client shutdown. A successfully initialized lifecycle explicitly reopens the existing registry; existing sessions remain preserved.

### 6.10 Branching Conversation Corruption Risk
If multiple clients concurrently reuse the same `conversation_id` and send differing messages:
* Because they are serialized, their messages will interleave sequentially rather than branching.
* There is a high risk of branching conversation corruption since there is no server-side history branching mechanism. The model will treat interleaving requests as one linear history, poisoning the context for all participating clients.

### 6.11 Asyncio Execution Assumptions
The lock safety and event-loop-safe guarantees of this concurrency model depend on the cooperative multitasking model of `asyncio`.
* Within the supported deployment model, SessionRegistry and SessionManager execute inside a single asyncio event loop per worker process.
* The atomic update of `active_streams` and lock acquisition relies on the fact that context switches only occur at explicit `await` boundaries.
* **Important**: This model assumes a single-event-loop execution environment. It is not designed or proven as a universal guarantee across arbitrary multi-threaded or multi-worker systems running without proper IPC/distributed locking.
