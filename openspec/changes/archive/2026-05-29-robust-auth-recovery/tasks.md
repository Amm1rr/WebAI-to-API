## 1. Core Exception Definitions

- [x] 1.1 Define `TransientSessionError` in `src/app/services/browser/errors.py` as a subclass of `WebAIRuntimeError` to track transient browser, page, and network-related delays.
- [x] 1.2 Ensure all required modules import `TransientSessionError` cleanly.

## 2. Stateless DOM-Based Authentication Verification Heuristics

- [x] 2.1 Refactor `GeminiProviderAdapter.check_authentication` to remain a lightweight, stateless, and fast DOM/navigation inspector. Do NOT embed any sleep, retry, or polling loops inside it.
- [x] 2.2 Ensure the adapter does NOT implement any cookie existence or cookie verification checks.
- [x] 2.3 Ensure any locator timeout, slow rendering, or network connection drop within `check_authentication` is caught and raised as a `TransientSessionError` instead of returning `False`.
- [x] 2.4 Ensure only a hard, confirmed authentication loss (stable redirect to sign-in page or sign-in button visible) returns `False`, raising `SessionNotAliveError`.

## 3. Session Recovery Hardening

- [x] 3.1 Verify that `ProviderSession._do_session_recovery` has no code paths that call `os.remove` on `self.state_path` under `TransientSessionError` or standard transient checks.
- [x] 3.2 Add explicit logging inside `_do_session_recovery` to clearly state when a transient context recreation is executed and trace the preserved `state_path`.

## 4. Pre-Submission Retry Loop & Lease Safety

- [x] 4.1 Update `GeminiPlaywrightProvider.chat_completions` to implement the 2.5-second redirect grace period (polling `check_authentication()` every 500ms) as an outer orchestration flow before proceeding to observer injection.
- [x] 4.2 Wrap the **pre-submission phase** (page acquisition, page readiness check, redirect grace period check, and observer injection) inside a retry loop (maximum 3 attempts).
- [x] 4.3 Ensure that any failed pre-submission attempt immediately calls the standard `_cleanup` block to release page leases and decrement the semaphore **before** initiating the backoff sleep.
- [x] 4.4 Capture `TransientSessionError` inside the retry loop, apply exponential backoff (1s, 2s, 4s), and log warnings.
- [x] 4.5 Place the **prompt submission boundary** (filling prompt, click submit, streaming start, and bridge streaming) strictly **OUTSIDE** and after the retry loop. Any post-submission failure SHALL propagate immediately without automatic retries.

## 5. Verification & Testing

- [x] 5.1 Run the full test suite (`poetry run pytest`) to ensure no regressions are introduced.
- [x] 5.2 Implement a unit test in `tests/test_recovery_orchestration.py` to mock a transient auth failure during the pre-submission phase and verify the exponential backoff retry and lease release behavior.
- [x] 5.3 Implement a unit test to verify that a transient failure does not delete the persistent `gemini.json` state file.
