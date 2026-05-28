## 1. Interface & Scripts Setup

- [ ] 1.1 Create `src/app/services/browser/base_adapter.py` defining the minimal `BaseProviderAdapter` interface.
- [ ] 1.2 Create `src/app/services/browser/adapters/scripts/gemini_scripts.py` and move `STREAM_EXTRACTOR_SCRIPT` and `STOP_OBSERVER_SCRIPT` constants into it.
- [ ] 1.3 Update `src/app/services/providers/gemini_playwright.py` to import both scripts from `gemini_scripts.py`.
- [ ] 1.4 Verify that the test suite runs and streaming completions function without errors.

## 2. Gemini Adapter Extraction

- [ ] 2.1 Create `src/app/services/browser/adapters/gemini_adapter.py` implementing the `BaseProviderAdapter` interface with minimal vendor details.
- [ ] 2.2 Implement `check_authentication`, `extract_conversation_id`, and `submit_prompt` in `GeminiProviderAdapter` using logic ported directly from `BrowserEngine` and `GeminiPlaywrightProvider`.
- [ ] 2.3 Update `GeminiPlaywrightProvider` to delegate DOM interactions and authentication checks to the adapter.
- [ ] 2.4 Verify that prompt typing, sending click sequences, and authentication expirations function identically.

## 3. Tab Decoupling

- [ ] 3.1 Create `src/app/services/browser/tab.py` and move `TabStatus`, `PersistentTab`, and `ManagedPage` into it.
- [ ] 3.2 Update import statements in `engine.py` and `gemini_playwright.py` to point to the new `tab` module.
- [ ] 3.3 Ensure the `asyncio.shield` block inside `ManagedPage.close()` is preserved exactly to prevent lock leaks.
- [ ] 3.4 Verify that request-scoped page leases and semaphore releases behave correctly under load.

## 4. Session Decoupling

- [ ] 4.1 Create `src/app/services/browser/session.py` and move `ProviderSession` and its background task loops (`_reaper_loop`, `_eviction_loop`, `_autosave_loop`, and the orphan delayed cleanup logic) into it.
- [ ] 4.2 Address circular engine-session references using explicit initializers or weak references.
- [ ] 4.3 Update imports in `engine.py` and `gemini_playwright.py`.
- [ ] 4.4 Verify that no asynchronous awaits reside under the synchronous `registry_lock` inside the new `session.py` module.
- [ ] 4.5 Verify that idle timeout eviction, active reaper evaluation, and state persistence loops run smoothly.

## 5. Orchestration Reduction

- [ ] 5.1 Reduce `src/app/services/browser/engine.py` to orchestrate only the browser process lifecycle and cross-session soft-cap limits.
- [ ] 5.2 Verify that `BrowserEngine.enforce_soft_cap()` safely accesses sessions and locks candidates during soft-cap pressure.
- [ ] 5.3 Verify browser generation rollover behavior by terminating the Chromium process and confirming old registry purges and context recreations.

## 6. Provider Registry Integration

- [ ] 6.1 Implement a basic, non-intrusive registry mapping provider names to resolved sessions inside `BrowserEngine`.
- [ ] 6.2 Execute the end-to-end test suite to confirm zero regressions across all operational modes, specifically validating cancellation, rollover, and streaming compatibility invariants.
