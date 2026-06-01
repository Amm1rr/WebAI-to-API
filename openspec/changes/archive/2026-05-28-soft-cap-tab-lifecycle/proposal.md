## Why

The system currently lacks a global upper bound for total live browser tabs. While request concurrency is limited by semaphores, resource-intensive persistent conversations and orphaned tabs can accumulate. A centralized "soft-cap" lifecycle policy in the `BrowserEngine` is needed to proactively manage total resource usage by evicting low-priority tabs across all sessions before they impact system stability.

## What Changes

- **Global Soft-Cap Configuration**: Add a configurable `max_total_tabs` setting to the Playwright section.
- **Centralized Eviction Enforcement**: Implement deterministic, best-effort eviction enforcement in the `BrowserEngine` layer.
- **Consistent Resource Tracking**: Ensure precise counting of all live Playwright pages (persistent, orphaned, and keepalive) across sessions.
- **Race-Hardened Eviction**: Implement a multi-phase eviction strategy that uses tab-private locks for candidate revalidation.
- **Overflow Support**: Allow the total tab count to temporarily exceed the soft-cap if all current tabs are healthy and actively leased.

## Capabilities

### New Capabilities
- `global-tab-soft-cap`: Manages the total lifecycle and population of browser tabs across all provider sessions using a centralized, prioritized eviction strategy.

### Modified Capabilities
- (None)

## Impact

- `src/app/services/browser/engine.py`: Primary implementation of the global enforcement and eviction logic.
- `config.conf`: New configuration parameters for the soft-cap policy.
- `src/app/services/browser/engine.py` (ManagedPage & PersistentTab): Updates to lifecycle hooks to support global counting.
