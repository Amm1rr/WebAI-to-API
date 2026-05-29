## Context

The current `ProviderSession` architecture allows concurrent requests to target the same `conversation_id`. Under high concurrency or network latency, this leads to:
1. Multiple requests simultaneously acquiring a lease on the same `PersistentTab`, resulting in interleaving reads/writes, corrupt stream state, and race conditions.
2. If a tab is busy, a second request falls back to creating a new browser page. When this new page is registered under the same `conversation_id`, the active tab of the first request is aggressively evicted/orphaned and closed mid-flight.
3. Rapid DEAD transitions of pages while they are actively being used by requests.

To eliminate these concurrency issues, we must enforce a strict, single-active-request mutual exclusion invariant per `conversation_id` with a fail-fast, non-queueing error response.

## Goals / Non-Goals

**Goals:**
* Enforce that at most one active in-flight request can operate on a single `conversation_id` at any time.
* Immediately reject concurrent duplicate requests targeting the same conversation with an HTTP 409 Conflict / `ConversationBusyError` without queueing, waiting, or retrying.
* Decouple the concurrency control mapping from the registry mutation lock to prevent lock contention, hidden ordering problems, and potential deadlocks.
* Perform ownership checking and reservation atomically before any lease acquisition or Playwright operations begin to prevent TOCTOU (Time-of-Check to Time-of-Use) races.
* Guard against failed acquisitions by rolling back ownership reservations safely only if the current request still owns the reservation.
* Implement robust stale-finalizer protection, ensuring older cleanup paths can never overwrite or clear active ownership held by newer requests.
* Keep the implementation lightweight, deadlock-free, and generation-safe.

**Non-Goals:**
* Inter-session concurrency management.
* Queueing concurrent requests (the architecture explicitly rejects queueing to ensure predictable low latency and prevent memory starvation).

## Decisions

### Decision 1: Session-Scoped Active Request Ownership Tracking
We will introduce an in-memory dictionary `self.active_conversations: Dict[str, str]` (mapping `conversation_id` to `request_id`) inside `ProviderSession`.
* **Single Authoritative Source**: `self.active_conversations` is the ONLY authoritative source of concurrency state. We will NOT check secondary, racier lifecycle states (such as tab status, registry contents, or tab lease status) to determine if a conversation is busy. If `conversation_id` is present in `active_conversations`, the conversation is busy.
* **Separation of Lifecycle Stages**: We explicitly distinguish between **ownership reservation** (which claims the conversation ID before acquisition) and **successful lease acquisition** (which actually obtains the browser page resources).

### Decision 2: Dedicated Concurrency Coordination Lock
To eliminate lock coupling, hidden lock-ordering bugs, and deadlocks, we will introduce a dedicated lock exclusively for conversation ownership coordination:
```python
self.conversation_lock = asyncio.Lock()
```
* **Strict Isolation of Locks**:
  * `conversation_lock` protects ONLY the conversation ownership state (`active_conversations`).
  * `registry_lock` remains responsible ONLY for persistent tab registry mutations (inserts, deletes, evictions).
* **No Lock Nesting / Simultaneous Acquisition**: The system MUST NEVER hold both `conversation_lock` and `registry_lock` simultaneously. Registry operations must never depend on ownership lock state, and ownership operations must never depend on registry lock state.
* **Deadlock Elimination**: By completely separating these two locks and never acquiring them together, we eliminate the risk of deadlocks between background sweeps (like eviction and reaper loops) and request-scoped finalization/rollback paths.

### Decision 3: Atomic Check-and-Reserve Phase
To prevent TOCTOU races, the busy check and reservation MUST be a single indivisible critical section.
* In `ProviderSession.acquire_lease()`, before any other operation begins (such as `ensure_healthy` or tab retrieval), we check busy status and reserve ownership inside a single critical section protected by `conversation_lock`:
```python
if conversation_id:
    async with self.conversation_lock:
        if conversation_id in self.active_conversations:
            raise ConversationBusyError(f"Conversation {conversation_id} is busy")
        self.active_conversations[conversation_id] = request_id
```
* Once this critical section finishes, any competing concurrent requests targeting the same `conversation_id` will immediately fail-fast with `ConversationBusyError`, eliminating any parallel lease acquisition attempts.

### Decision 4: Guarded Reservation Rollback on Failure
If lease acquisition, page setup, or registration subsequently fails after ownership has been reserved, the system must execute a guarded rollback to clear the reservation.
* The rollback MUST be executed in `acquire_lease`'s outer `except BaseException` block.
* To prevent a failing stale request from rolling back ownership belonging to a newer request, the rollback MUST be conditional and protected by `conversation_lock`:
```python
except BaseException as e:
    if conversation_id:
        async with self.conversation_lock:
            if self.active_conversations.get(conversation_id) == request_id:
                self.active_conversations.pop(conversation_id, None)
```

### Decision 5: Conditional Lease Release in ManagedPage
We will extend `ManagedPage` to store `request_id` and track conversation finalization.
* `ManagedPage` will receive `request_id` during initialization.
* **Conditional Release**: During shielded cleanup (`_do_close`), `ManagedPage` will remove the conversation ownership mapping from `active_conversations` under `conversation_lock` ONLY if it is verified that `self.active_conversations.get(conversation_id) == request_id`.

### Decision 6: Stale-Finalizer Protection
* **Stale-Finalizer Protection**: Stale cleanup/finalization paths are non-authoritative. If an older request's finalization path executes and attempts to clear ownership, the conditional check under `conversation_lock` detects the mismatch (as a newer request's `request_id` is registered) and silently aborts the mutation without throwing errors or clearing the active ownership.

### Decision 7: Comprehensive Concurrency Test Suite
We will add a dedicated, robust concurrency test suite `tests/test_conversation_concurrency.py` verifying:
1. **Overlap Rejection**: Verifies that two concurrent requests for the same `conversation_id` cannot both pass the reservation phase; the second request must fail-fast with HTTP 409 Conflict.
2. **Failed Acquisition Rollback**: Verifies that if lease acquisition fails, the reservation is successfully rolled back under `conversation_lock` and leaves no busy state residue.
3. **Guarded Overwrite Protection**: Verifies that a stale rollback or cleanup path from an older request is blocked from clearing or mutating a newer active ownership registered under a different `request_id`.
4. **Lock Separation and Deadlock Prevention**: Verifies that ownership rollback/finalization paths do not require registry access and cannot deadlock with registry operations.
5. **Resilience**: Verifies that the ownership mapping remains correct and clean under task cancellation, timeout, and overlapping teardown.

## Risks / Trade-offs

* **[Risk]**: Exception or cancellation during lease release leading to leaked ownership (orphan busy states).
  * **Mitigation**: Wrap the active conversation ownership release inside the already existing, robust, cancellation-shielded `ManagedPage._do_close_safely()` method using `asyncio.shield`. Ensure the release is executed in `finally` blocks under both successful and failed paths in `acquire_lease`.
