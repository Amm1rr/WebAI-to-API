## Context

The WebAI-to-API runtime utilizes a multi-layered Playwright process orchestrator:
* **BrowserEngine**: Global process singleton and terminal shutdown manager.
* **ProviderSession**: Provider-specific container for contexts, keepalive pages, and background sync loops.
* **ManagedPage**: Request-scoped permit/lease container.
* **PersistentTab**: Long-lived tab container serialized through authoritative locking.

The codebase suffers from fragmented error handling, silent queue overflows, unshielded lock entries, and a liveness mismatch bug where browser generation rollovers trigger terminal engine shutdowns via the reaper loop. This document designs the technical solutions to incrementally harden the error handling and recovery model.

## Goals / Non-Goals

**Goals:**
* Correct the `_reaper_loop` rollover mismatch bug in `session.py` to prevent plan-driven browser relaunches from triggering global engine shutdowns.
* Enforce stream integrity by ensuring that event queue saturation is treated as a terminal request-scoped failure that fails deterministically. Queue saturation MUST terminate the active request deterministically and MUST invalidate the active request stream state, while avoiding page poisoning unless runtime integrity checks require it.
* Ensure cancellation-safe teardown inside ManagedPage lease release, including lock acquisition waits and resource release operations, prioritizing deterministic cleanup and strongly securing semaphore/lease release under normal task cancellation, while auxiliary Playwright cleanup remains best-effort.
* Ensure tab page closure and status updates are serialized through authoritative locking.
* Establish a minimal, lifecycle-focused exception hierarchy in `src/app/services/browser/errors.py`.
* Protect boundary layers by forcing provider scripts to escalate recovery signals via semantic exceptions rather than directly manipulating session contexts or state files.

**Non-Goals:**
* No broad redesign of the global lock hierarchy or runtime architecture.
* No replacement of the Playwright-native browser backend.
* No global rewriting of generic catch blocks outside of lifecycle-critical or request-scoped boundaries.
* No conversion of all broad catch blocks during this migration.
* No disruption of legacy or transitional provider integrations, which must maintain compatibility during rollout.


## Decisions

### Decision 1: Custom Exception Hierarchy in `src/app/services/browser/errors.py`
We will define a minimal, lifecycle-focused browser-native hierarchy subclassing `WebAIRuntimeError` to enable incremental migration (where generic `RuntimeError` may temporarily coexist):
* `BrowserShuttingDownError`: Attempting actions after shutdown has initiated.
* `BrowserDisconnectedError`: The underlying browser process crashed.
* `BrowserGenerationMismatchError`: Operating on a tab from a previous generation.
* `SessionNotAliveError`: The liveness probe of the context keepalive page failed.
* `LeaseInvalidatedError`: Attempting operations on an invalidated lease.
* `QueueOverflowError`: Event queue saturation during bridge event enqueuing.

The hierarchy is intentionally restricted to lifecycle and runtime coordination concerns during the initial migration. Other domain-specific exceptions (such as provider-domain errors) are deferred to future extensions.

* **Alternative Considered**: Extend Python built-ins like `RuntimeError`.
* **Rationale**: Custom exceptions provide unambiguous stack traces and enable specific error classification on recovery boundaries without breaking legacy compatibility.

### Decision 2: Reaper Loop Rollover Bug Resolution
We will modify `ProviderSession._reaper_loop()` to handle generation rollovers gracefully. If `last_browser_generation != engine.browser_generation`, the reaper loop MUST ignore the stale generation state, MUST NOT trigger engine shutdown, and MUST yield recovery responsibility exclusively to the authoritative session recovery flow.

* **Alternative Considered**: Run active liveness checks in a separate background thread.
* **Rationale**: Bypassing liveness on generation mismatches naturally yields to `acquire_lease()`'s authoritative `ensure_healthy()` recovery flow without complex inter-thread checks.

### Decision 3: Cancellation-Safe Teardown in ManagedPage Release
We will design ManagedPage teardown to be cancellation-safe. Both the lock acquisition wait and the subsequent resource release operations must be shielded from task cancellation. This ensures that even if a request is cancelled while waiting to enter the lease release block, the release logic executes to completion to prevent permit leaks. Wrapping both lock entry and resource release in a shielded block ensures that cleanup of the semaphore and lease release paths is best-effort guaranteed and strongly secured under normal task cancellation, while auxiliary Playwright cleanup remains best-effort.

* **Alternative Considered**: Keep the lock unshielded and swallow `CancelledError`.
* **Rationale**: Wrapping both lock entry and resource release in a shielded block ensures that teardown prioritizes semaphore and lease release, securing their cleanup under normal task cancellation.

### Decision 4: Authoritative Recovery Ownership Boundaries
We will strictly partition recovery and lifecycle authority:
* **Providers**: Responsible only for detecting failures and escalating them via recovery-scoped exceptions. Providers must not directly manage storage state files or invoke context recreation.
* **ProviderSession**: Exclusive owner of session-scoped recovery execution (including context recreation and state purges).
* **BrowserEngine**: Exclusive owner of terminal shutdown authority.

* **Alternative Considered**: Allow providers to manage state deletion but delegate context setup.
* **Rationale**: Preserves strict ownership boundaries by preventing providers from orchestrating recovery flows directly.

## Risks / Trade-offs

* **[Risk] Cancellation Race in Shielded Tasks** → *Mitigation*: Ensure all task cleanup continues in a best-effort manner, prioritizing semaphore and lock returns even if auxiliary Playwright evaluations fail.
* **[Risk] Deadlocks in Multi-Lock Recovery Paths** → *Mitigation*: Strictly adhere to the lock hierarchy: Engine `management_lock` → Session `init_lock` → Session `registry_lock` → Tab `_lock`. Never acquire locks out of order or under registry lock awaits.
* **[Risk] Stale Generation Reuse** → *Mitigation*: Ensure that runtime operations interacting with browser-owned resources validate generation consistency at authoritative lifecycle boundaries rather than hot-path check polling.

