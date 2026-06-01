## 1. Infrastructure & State Tracking

- [x] 1.1 Update `config.conf.example` and `config.py` to include `max_total_tabs`.
- [x] 1.2 Implement `ProviderSession.get_eviction_candidates()`: return lists of `(status, tab)` under `registry_lock`.
- [x] 1.3 Implement `ProviderSession.page_count` property: (registry size + active orphans + internal pages).

## 2. Centralized Engine Enforcement

- [x] 2.1 Implement `BrowserEngine.total_page_count` aggregation logic (exclude only `DEAD`).
- [x] 2.2 Implement `BrowserEngine.enforce_soft_cap()`:
    - Collect candidates from all sessions under `registry_lock`.
    - Sort by priority: 1. `INVALIDATING` (Orphans), 2. `IDLE` (LRU), 3. `STALE LEASED`.
    - Release all registry locks.
    - Iterate candidates: acquire `tab._lock`, verify eligibility, transition to INVALIDATING under tab._lock, release the lock, then await tab.close().
- [x] 2.3 Ensure `tab.close()` is best-effort and exception-safe during eviction.

## 3. Integration & Observability

- [x] 3.1 Hook `engine.enforce_soft_cap()` into `ProviderSession.acquire_lease()` specifically before the "New Tab Flow" (`self.context.new_page()`).
- [x] 3.2 Add logging for "Soft-cap pressure" warnings and detailed "Eviction event" logs.

## 4. Verification

- [x] 4.1 Create a stress test script that opens multiple persistent sessions.
- [x] 4.2 Verify that `total_page_count` stays near `max_total_tabs` under load.
- [x] 4.3 Verify the lock hierarchy invariant: `registry_lock > tab._lock`.
- [x] 4.4 Confirm no lock starvation or deadlocks occur during simultaneous lease acquisitions and evictions.
