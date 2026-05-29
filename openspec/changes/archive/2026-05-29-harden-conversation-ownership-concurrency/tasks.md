## 1. Concurrency Exceptions and API Setup

- [x] 1.1 Define `ConversationBusyError` subclassing `RequestError` in `src/app/services/browser/errors.py`
- [x] 1.2 Register `ConversationBusyError` in `src/app/services/providers/gemini_playwright.py` exception handler block, translating it directly to HTTP 409 Conflict with a clear message

## 2. Active Concurrency Tracking and Dedicated Concurrency Lock in ProviderSession

- [x] 2.1 Add `self.conversation_lock = asyncio.Lock()` and `self.active_conversations: Dict[str, str] = {}` to `ProviderSession.__init__` in `src/app/services/browser/session.py`. Document that `conversation_lock` protects ONLY `active_conversations` and MUST NOT be held simultaneously with `registry_lock`
- [x] 2.2 In `ProviderSession.acquire_lease()`, implement the atomic check-and-reserve phase under `self.conversation_lock` before any other asynchronous operation. Raise `ConversationBusyError` immediately if `conversation_id` is present in `active_conversations`
- [x] 2.3 Ensure `active_conversations` is the single authoritative concurrency source. Do NOT check any secondary states (tab status, registry, or tab lease status)
- [x] 2.4 In `ProviderSession.acquire_lease()`, implement guarded reservation rollback inside the outer `except BaseException` block: remove the reservation from `active_conversations` under `conversation_lock` ONLY if it currently maps to the failing request's `request_id`
- [x] 2.5 Harden `ProviderSession.register_conversation()` to conditionally check and register conversation ownership under `conversation_lock` for newly assigned conversation IDs, ensuring safety against collisions

## 3. ManagedPage Conditional Lease Release & Stale-Finalizer Protection

- [x] 3.1 Extend `ManagedPage` in `src/app/services/browser/tab.py` to accept and track `request_id`
- [x] 3.2 In `ManagedPage._do_close()`, implement conditional release: remove the conversation from `session.active_conversations` under `conversation_lock` ONLY if it currently maps to the same `request_id`. Shield the release with `asyncio.shield`
- [x] 3.3 Implement stale-finalizer protection: ensure that if a stale cleanup path from an older request attempts to clear or mutate ownership, it detects that the current owner is a newer request and silently aborts its mutation under `conversation_lock` without throwing errors or clearing the active ownership
- [x] 3.4 Audit tab invalidation and eviction loops to check active leased status and ownership, protecting active leased tabs from being aggressively closed or reaped by overlapping request finalization paths

## 4. Verification and Concurrency Tests

- [x] 4.1 Write integration tests in a new file `tests/test_conversation_concurrency.py` verifying that two concurrent requests targeting the same `conversation_id` cannot both pass the reservation phase; the second request must fail-fast with HTTP 409 Conflict
- [x] 4.2 Verify in tests that a failed or aborted lease acquisition properly rolls back the reservation under `conversation_lock` and leaves no ownership residue in `active_conversations`
- [x] 4.3 Add ownership overwrite protection tests: verify that a stale rollback or cleanup path (from an old request) is blocked from clearing or mutating a newer active ownership registered under a different `request_id`
- [x] 4.4 Add lock separation tests: verify that ownership rollback/finalization paths do not require registry access, do not acquire `registry_lock`, and cannot deadlock with registry operations
- [x] 4.5 Verify that the ownership mapping remains correct and clean under task cancellation, timeout, and overlapping teardown, and all 24+ tests compile and pass successfully
