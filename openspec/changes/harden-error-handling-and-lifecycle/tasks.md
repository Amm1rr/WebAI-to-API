## 1. Phase 1: Critical Bug Fixes and Invariant Hardening

- [ ] 1.1 Fix the `_reaper_loop` in `src/app/services/browser/session.py` so that it MUST ignore stale generation state, MUST NOT trigger engine shutdown, and MUST yield recovery responsibility exclusively to the authoritative session recovery flow.
- [ ] 1.2 Modify stream buffers in `src/app/services/providers/gemini_playwright.py` so that event queue saturation MUST terminate the active request deterministically, MUST invalidate the active request stream state, and the callback MUST NOT silently drop events (avoiding page poisoning unless runtime integrity checks require it).
- [ ] 1.3 Refactor ManagedPage teardown in `src/app/services/browser/tab.py` to ensure cancellation-safe execution of lock acquisition waits and resource release operations, prioritizing deterministic cleanup and strongly securing semaphore/lease release under normal task cancellation, while auxiliary Playwright cleanup remains best-effort.
- [ ] 1.4 Serialize tab status and closure mutations in `src/app/services/browser/tab.py` through authoritative locking to eliminate concurrent closure race conditions.

## 2. Phase 2: Semantic Exception Hierarchy Integration

- [ ] 2.1 Create `src/app/services/browser/errors.py` and define the minimal custom exception classes (`BrowserShuttingDownError`, `BrowserDisconnectedError`, `BrowserGenerationMismatchError`, `SessionNotAliveError`, `LeaseInvalidatedError`, and `QueueOverflowError`) subclassing `WebAIRuntimeError`, ensuring the hierarchy is lifecycle-scoped only.
- [ ] 2.2 Replace generic `RuntimeError` usages inside `src/app/services/browser/session.py` and `src/app/services/browser/engine.py` with specific exceptions from the minimal hierarchy.
- [ ] 2.3 Refactor runtime operations interacting with browser-owned resources in `src/app/services/providers/gemini_playwright.py` to validate generation consistency at concrete enforcement points: lease acquisition, stream generators, and background recovery loops.

## 3. Phase 3: Recovery Boundary Hardening

- [ ] 3.1 Remove direct state file deletions and context recovery setups from provider modules.
- [ ] 3.2 Implement recovery escalation from providers using recovery-scoped exceptions, and handle them inside `src/app/services/browser/session.py` as the sole recovery executor.

## 4. Phase 4: Observability and Structured Error Propagation

- [ ] 4.1 Update catch blocks in `session.py`, `engine.py`, and `tab.py` to log with enriched contextual metadata (such as `generation`, `tab_id`, `request_id`).
- [ ] 4.2 Audit and prune broad catch-all statements across cleanup and shutdown routines where they interfere with correct error classification.
- [ ] 4.3 Update `docs/error-policy.md` to formally document the minimal semantic exception model, strict recovery boundaries, and cancellation rules.


