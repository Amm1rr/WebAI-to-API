## Context

The `WebAI-to-API` project uses Playwright to drive Google Gemini web app sessions headless in Docker. The session state is persisted inside `auth_state/gemini.json`. 

Currently, transient errors (e.g., slow networks, locator timeouts, or page redirects during navigation) are treated as fatal authentication expirations (`SessionNotAliveError`), which triggers the destructive recovery process that deletes the user's persistent `gemini.json` file. 

This design introduces a granular authentication recovery mechanism that classifies failures into transient and hard failures based strictly on DOM and navigation states, retries transient pre-submission failures with exponential backoff, scores Google redirects to prevent false-positives, and protects the persistent session file from deletion.

## Goals / Non-Goals

**Goals:**
- Differentiate transient failure conditions (e.g., locator timeouts, page load delays, connection drops) from hard authentication expirations (stable redirects to sign-in UI).
- Ensure that the persistent session file `auth_state/gemini.json` is never deleted under transient failures.
- Implement an exponential backoff retry policy (up to 3 retries) restricted strictly to the **pre-submission phase** (auth checks, page readiness checks, locator acquisition, observer preparation).
- Keep `GeminiProviderAdapter.check_authentication` lightweight, stateless, and side-effect-free.
- Implement a 2.5-second redirect grace-period loop (polling every 500ms) executed as a bounded **outer orchestration flow** in the provider layer, not inside the adapter.
- Ensure that failed pre-submission attempts **fully release page leases and resources** before initiating a retry, preventing stale lease holding and lock contention.
- Maintain existing concurrency, locking, and tab lease guarantees.

**Non-Goals:**
- Do NOT perform any cookie existence or cookie inspection checks for authentication status.
- Do NOT interpose any response-layer or network-layer verification (e.g., header inspection, network interception, API calls). The recovery model MUST remain DOM/navigation-based only.
- Do NOT allow any automatic retries after the prompt submission boundary is crossed (prompt fill, clicking submit, or bridge streaming started).
- Do NOT hold onto stale or failed page leases across retry iterations.

## Decisions

### Decision 1: Authentication Failure Classification & Stateless Adapter
* **Option A**: Treat all errors as hard failures and re-authenticate (Discarded).
* **Option B**: Introduce a DOM/navigation-based dual-classification. Use a new exception `TransientSessionError` for timeouts, network drops, and DOM loading lag, while preserving `SessionNotAliveError` strictly for a confirmed, stable Google accounts sign-in page. `GeminiProviderAdapter.check_authentication` will remain stateless, immediate, and free of sleeps or polling loops. (Selected).
* **Rationale**: This prevents transient issues from triggering the destructive recovery path while keeping the adapter design lightweight and maintainable.

### Decision 2: Preservation of `gemini.json` on Transient Failures
* **Option A**: Delete `gemini.json` on all escalated recovery operations to ensure a clean slate (Discarded).
* **Option B**: Modify `_do_session_recovery` to never call `os.remove` under `TransientSessionError` or standard transient checks. Preserve the file, clear in-memory contexts/leases, and recreate the context using the existing `gemini.json` file. (Selected).
* **Rationale**: Preserving the file allows the session to heal itself seamlessly once network connectivity or element rendering restores.

### Decision 3: Narrow Pre-Submission Retry Gating & Lease Safety
* **Option A**: Implement a general retry loop covering the entire completions request (Discarded).
* **Option B**: Implement a retry loop inside `GeminiPlaywrightProvider.chat_completions` that catches `TransientSessionError` during the pre-submission setup (page acquisition, page readiness, auth check, and observer injection) up to 3 times with exponential backoff (1s, 2s, 4s). 
  - Each failed iteration MUST execute the full `_cleanup` flow to release the active `ManagedPage` lease and decrement the semaphore before sleeping and starting a new retry.
  - If any failure occurs *after* prompt submission starts (filling the prompt, clicking submit, or active streaming), immediately fail-fast without any retries. (Selected).
* **Rationale**: Gating retries strictly before prompt submission prevents duplicate prompt submissions and inconsistent streaming bridge states, while immediate lease release prevents request starvation and semaphore deadlock.

### Decision 4: Redirect Grace Period in Outer Orchestration
* **Option A**: Implement redirect polling inside the adapter (Discarded).
* **Option B**: Implement a bounded polling loop (maximum 2.5 seconds, checking every 500ms) in `GeminiPlaywrightProvider.chat_completions`. If `check_authentication()` returns `False` due to a Google redirect URL, wait and poll `check_authentication()` again to check if the browser successfully returns to the Gemini chat interface. If it resolves back, proceed. If it remains on the sign-in screen after 2.5 seconds, escalate to hard failure. (Selected).
* **Rationale**: This separates navigation orchestration concerns (provider layer) from raw DOM state inspection concerns (adapter layer).

## Risks / Trade-offs

- **[Risk] Duplicate prompt submission or stream corruption** → **Mitigation**: Retries are strictly blocked once prompt submission begins. Any post-submission failure terminates the request immediately and safely without retries.
- **[Risk] Lease leaks/contention during retries** → **Mitigation**: Every failed retry attempt immediately executes the standard cleanup block, releasing the lease and slotting before initiating the backoff sleep and subsequent fresh lease acquisition.
- **[Risk] Increased pre-submission latency** → **Mitigation**: Limit the maximum backoff to a safe total timeout (e.g., 7 seconds total across 3 retries) and respect the `total_request_timeout` configuration.
