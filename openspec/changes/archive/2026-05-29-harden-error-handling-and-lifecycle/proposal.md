## Why

The Playwright-based browser runtime in WebAI-to-API relies on a mixture of generic exceptions, broad catch-all blocks, and un-synchronized state transitions. This leads to several runtime issues:
* Stale generation rollover mismatch that triggers engine-scoped terminal shutdown in the reaper loop.
* Silent event queue overflow that drops text chunks and corrupts streams without failing requests.
* Unshielded lock acquisition waits during teardown that leak semaphore permits and lock ownership under task cancellation.
* Un-synchronized tab status transitions.
* Boundary violations where providers execute recovery actions or delete state files directly.

Hardening these behaviors is necessary to prevent resource leaks and state corruption under concurrency pressure, ensuring the runtime aligns with established architectural contracts.

## What Changes

This change implements incremental hardening of the error handling, lifecycle recovery, and cancellation safety of the Playwright-based browser runtime.

Specific modifications include:
* **Critical Bug Fixes**: Correcting the reaper loop rollover handling, implementing deterministic request failure on queue overflow, shielding lock acquisition waits during tab release, and synchronizing tab status transitions.
* **Minimal Semantic Exception Hierarchy**: Defining a minimal, lifecycle-focused set of browser-native exceptions in `browser/errors.py` (including `BrowserShuttingDownError`, `BrowserDisconnectedError`, `BrowserGenerationMismatchError`, `SessionNotAliveError`, `LeaseInvalidatedError`, and `QueueOverflowError`) to incrementally replace generic `RuntimeError` usage on lifecycle boundaries.
* **Recovery Authority Boundaries**: Clarifying recovery boundaries so that providers detect and escalate failures, while `ProviderSession` owns session-scoped recovery execution and `BrowserEngine` owns terminal shutdown authority.
* **Cancellation-Safe Teardown**: Prioritizing deterministic cleanup and strongly securing semaphore/lease release under normal task cancellation, while auxiliary Playwright cleanup is executed as best-effort.
* **Incremental Integration**: Ensuring compatibility with legacy or transitional provider paths remains intact, allowing generic `RuntimeError` to temporarily coexist during rollout.

## Capabilities

### New Capabilities
- `error-and-lifecycle-hardening`: Hardened exception boundaries, strict recovery authority, stream queue overflow validation, and cancellation-safe resource cleanups.

### Modified Capabilities

## Impact

The following modules will be affected:
* `src/app/services/browser/session.py` (Reaper loop, recovery, setup)
* `src/app/services/browser/tab.py` (Lease validation, status synchronization, ManagedPage teardown)
* `src/app/services/browser/engine.py` (Shutdown synchronization, exception checks)
* `src/app/services/providers/gemini_playwright.py` (Bridge callbacks, cleanup, recovery escalation)
* `src/app/services/browser/adapters/*` (Authentication and submission error handling)
* `docs/error-policy.md` (Update policies to match the new behavior)

