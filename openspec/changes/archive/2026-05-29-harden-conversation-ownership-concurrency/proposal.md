## Why

The current WebAI-to-API browser runtime does not enforce request mutual exclusion for the same persistent conversation. Multiple concurrent requests targeting the same `conversation_id` are permitted to acquire and operate on the same persistent tab simultaneously. This creates critical concurrency races and lifecycle conflicts across submit, stream handling, cleanup, lease release, and tab invalidation flows. For instance, finalization of an earlier request can invalidate or close a tab while a concurrent request is actively reading from or writing to it, leading to deadlocks, state corruption, and leaked browser resources. This architectural gap must be solved immediately by ensuring only one active request can own a conversation session at any given time.

## What Changes

To ensure conversation-ownership safety, the system will introduce strict mutual exclusion constraints at the conversation level:
- **Single-Active-Request Invariant**: Each `conversation_id` is restricted to exactly one active in-flight request at a time, determined exclusively by an active ownership mapping.
- **Fail-Fast Rejection**: Concurrent requests targeting a conversation already in-flight will be rejected immediately with a semantic error (HTTP 409 Conflict / `ConversationBusyError`), with no internal queueing or retry loops.
- **Dedicated Concurrency Lock**: A dedicated lock `conversation_lock` will be introduced exclusively for conversation ownership coordination, protecting ONLY `active_conversations` and decoupling it entirely from the tab `registry_lock`. Both locks MUST never be held simultaneously.
- **Atomic Check-and-Reserve Phase**: Before lease acquisition begins, the check for busy status and ownership reservation must be executed as a single indivisible critical section under `conversation_lock`. Competing requests targeting the same conversation will fail-fast immediately.
- **Guarded Rollback**: If lease acquisition subsequently fails, the ownership reservation will be safely rolled back in a guarded cleanup path under `conversation_lock`, only if the current request is still the registered owner of the reservation.
- **Robust Conditional Release**: Active request ownership will be tracked atomically and released safely in all exit scenarios under `conversation_lock`. Ownership release is conditional; stale cleanup/finalization paths are non-authoritative and are strictly prevented from clearing ownership belonging to newer requests.

## Capabilities

### New Capabilities
- `conversation-concurrency-limits`: Establishes authoritative, request-scoped mutual exclusion and session ownership per `conversation_id` across the lifecycle.

### Modified Capabilities
<!-- None -->

## Impact

- **API Layer**: Introduces HTTP 409 Conflict response when a conversation is already in use.
- **Browser/Session Layer (`ProviderSession`, `PersistentTab`, `ManagedPage`)**:
  - Adds `active_conversations` mapping and a dedicated `conversation_lock` as the single authoritative concurrency coordination domain.
  - Decouples `conversation_lock` from `registry_lock`, eliminating lock contention and deadlock risks.
  - Implements the atomic check-and-reserve phase in `acquire_lease()` under `conversation_lock` before any async yields.
  - Guarantees guarded reservation rollback on failure under `conversation_lock`, leaving no busy state residue.
  - Implements stale-finalizer protection under `conversation_lock` so only the current owner may mutate ownership state, and stale paths silently abort ownership mutation.
  - Ensures robust release mechanisms using `asyncio.shield` in finalization.
- **Testing**: Adds dedicated concurrency tests validating that concurrent requests cannot both pass reservation, failed acquisition properly rolls back reservation, rollback cannot clear ownership belonging to a newer request, and no deadlocks are introduced.
