# global-tab-soft-cap Specification

## Purpose
TBD - created by archiving change soft-cap-tab-lifecycle. Update Purpose after archive.
## Requirements
### Requirement: Centralized Soft-Cap Enforcement
The `BrowserEngine` SHALL serve as the central authority for enforcing the global browser page soft-cap across all provider sessions.

#### Scenario: Enforce cap before new page creation
- **WHEN** a `ProviderSession` needs to create a new page and calls `engine.enforce_soft_cap()`
- **THEN** the `BrowserEngine` SHALL evaluate the global `active_page_count` and trigger prioritized eviction if `max_total_tabs` is exceeded.

### Requirement: Global Resource Accounting
The system SHALL track all live browser resources (persistent, orphaned, and keepalive pages). A tab SHALL be excluded from `active_page_count` only when its status is `DEAD`.

#### Scenario: Status-based accounting
- **WHEN** the `active_page_count` is calculated
- **THEN** it SHALL include all tabs in `IDLE`, `LEASED`, or `INVALIDATING` status, and SHALL EXCLUDE any tab in `DEAD` status.

### Requirement: Prioritized Eviction Strategy
When the soft-cap is reached, the engine SHALL prioritize evicting resources that are non-essential or unresponsive.

#### Scenario: Eviction candidate selection
- **WHEN** global soft-cap is exceeded
- **THEN** the engine SHALL prioritize candidates in the following order:
  1. `INVALIDATING` (Orphans)
  2. `IDLE` (Persistent conversations by LRU)
  3. `STALE LEASED` (Heartbeat > `lease_timeout`)
- **AND** it SHALL NEVER aggressively kill a healthy `LEASED` tab with a fresh heartbeat.

### Requirement: Concurrency and Lock Invariants
The system SHALL adhere to strict lock hierarchy and non-blocking I/O rules to prevent deadlocks and lock starvation.

#### Scenario: Lock hierarchy enforcement
- **WHEN** performing concurrency operations
- **THEN** the system SHALL follow the hierarchy: `registry_lock > tab._lock`.
- **AND** no Playwright RPC SHALL occur while holding `registry_lock` or `tab._lock`.
- **AND** `registry_lock` MUST NEVER be acquired while holding `tab._lock`.

### Requirement: Race-Safe Candidate Revalidation
Any eviction candidate identified under a registry lock MUST be revalidated under its private lock immediately before physical closure.

#### Scenario: Atomic revalidation
- **WHEN** a tab is selected for eviction
- **THEN** the engine SHALL acquire the `tab._lock`, verify that the status is still eligible for eviction (e.g., `IDLE`), and transition the tab to `INVALIDATING` status.
- **AND** the system SHALL release the `tab._lock` BEFORE calling `await tab.close()`.
- **AND** the system SHALL ensure the tab is no longer leasable once in `INVALIDATING` status.
- **AND** `tab.close()` SHALL be best-effort and exception-safe.

