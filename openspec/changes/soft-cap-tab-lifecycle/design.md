## Context

The `BrowserEngine` orchestrates multiple `ProviderSession` instances. While each session has a semaphore to limit concurrent requests, there is no global limit on the total number of browser pages. This can lead to resource exhaustion from accumulated persistent conversations and orphaned tabs.

## Goals / Non-Goals

**Goals:**
- Implement a global "soft-cap" on the total number of browser pages.
- Centralize enforcement in `BrowserEngine` for cross-session coordination.
- Prioritize eviction of non-essential resources (orphans, idle tabs, and stale leases).
- Protect healthy, actively leased tabs from aggressive eviction.
- Ensure strict deadlock prevention and race-hardening.

**Non-Goals:**
- Implement a "hard-cap" that rejects requests.

## Decisions

### 1. Centralized Global Enforcement
`BrowserEngine` will provide a method (e.g., `enforce_soft_cap`) that sessions MUST call before creating a new page.
- **Rationale**: Only the engine has cross-session visibility to safely decide which session's resources should be sacrificed to stay under the cap.

### 2. Consistent Page Counting
`active_page_count` aggregation rule:
`total = sum(tab for tab in all_tabs if tab.status != TabStatus.DEAD)`
- **Counted States**: `IDLE`, `LEASED`, `INVALIDATING`.
- **Timing**: `time.monotonic()` remains the timing source for all duration-based logic.

### 3. Deterministic Eviction Priority
Only tabs with a live physical page are candidates for eviction:
1. **`INVALIDATING` (Orphans)**: Tabs already removed from the registry and in cleanup cooldown.
2. **`IDLE` (Persistent)**: Oldest conversation tabs (LRU).
3. **`STALE LEASED`**: Tabs where heartbeat > `lease_timeout` (emergency recovery).

### 4. Locked Revalidation & Best-Effort Closure
To ensure race-safety, candidates are revalidated under their private lock. `PersistentTab.close()` is best-effort and exception-safe.
- **Atomic Revalidation Flow**:
    1. Acquire `tab._lock`.
    2. Verify status is still eviction-eligible.
    3. Transition tab to `INVALIDATING`.
    4. Release `tab._lock`.
    5. `await tab.close()`.
A tab in INVALIDATING state MUST NOT become leasable again.
- **Rationale**: Revalidation and transition to `INVALIDATING` under `tab._lock` prevents stealing by concurrent leases, while releasing it before `await` avoids lock starvation during Playwright RPCs.

### 5. Lock Ordering Invariants
- **Hierarchy**: `registry_lock` > `tab._lock`.
- **INVARIANT**: No Playwright RPC may occur while holding `registry_lock` or `tab._lock`.
- **INVARIANT**: `registry_lock` MUST NEVER be acquired while holding `tab._lock`.

## Risks / Trade-offs

- **[Risk] High Churn** → Frequent eviction/reload if cap is too low.
    - **Mitigation**: Pressure logging and sensible defaults.
- **[Risk] Lock Starvation** → Resolved by releasing locks before slow Playwright RPCs.
