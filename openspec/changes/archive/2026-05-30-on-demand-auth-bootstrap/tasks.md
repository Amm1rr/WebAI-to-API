## 1. Authentication Status Layer (`AuthManager`)

- [x] 1.1 Create `src/app/services/browser/auth_manager.py` to define the unified authentication status data models and states.
- [x] 1.2 Implement status checkers for `gemini-webapi` (`AUTHENTICATED`, `GUEST`, `INVALID`) based on direct account status checks.
- [x] 1.3 Implement fast status checkers for Playwright sessions (`VALID_SESSION`, `NO_SESSION`, `EXPIRED_SESSION`, `INVALID_STATE`) checking the persistent storage file.
- [x] 1.4 Cache status in memory and refresh asynchronously on application startup lifespan and upon on-demand login completion.
- [x] 1.5 Add hooks to passive-refresh the cached Playwright status to `EXPIRED_SESSION` immediately upon catching a `SessionNotAliveError` during chat completions.

## 2. Concurrency Coordination & Locks

- [x] 2.1 Implement `login_lock` and the active state machine (`IDLE`, `LOGIN_IN_PROGRESS`) inside the `AuthManager` service class.
- [x] 2.2 Define the `AuthCoordinationLock` abstract base class and implement a thread-safe process-bound concrete `InMemoryAuthLock` as the MVP default.
- [x] 2.3 Add configuration boundary `auth_lock_backend = in_memory` under `[Playwright]` with warning logs triggered if non-implemented backends are requested.
- [x] 2.4 Implement process-concurrency warning checks inside `AuthManager` to log an honest warning if multiple workers (`WEB_CONCURRENCY` or `WORKERS` > 1) are detected under `in_memory`.
- [x] 2.5 Add coordination lock checks in standard completions `/v1/chat/completions` (delegating to `auth_mgr.coordination_lock.is_locked()`) to fail-fast with HTTP 503 during active logins.
- [x] 2.6 Ensure standard completions immediately return HTTP 401 on expired auth *only* when the target request requires an authenticated session, allowing guest fallback for other requests.

## 3. On-Demand Interactive Login Workflow

- [x] 3.1 Refactor the headful sign-in monitoring logic from `verify_login.py` into a reusable coordinator method `AuthManager.run_login_flow`.
- [x] 3.2 Implement the isolated bootstrap browser launch flow inside `AuthManager.run_login_flow` using only the existing primitives of `BrowserEngine` (e.g. `BrowserEngine.get_browser_engine(headless=False, is_bootstrap=True)`).
- [x] 3.3 Set up the background DOM monitoring loop to check for Gemini input textbox visibility (`SELECTORS["INPUT"]`) as the login completion heuristic.
- [x] 3.4 Ensure atomic state serialization inside `ProviderSession.save_state` to write to `gemini.json` via a temporary file with physical `fsync` and replace.
- [x] 3.5 Ensure deterministic lifecycle teardown: close login pages and terminate isolated browser context under all outcomes (success, timeout, cancel, crash) using existing lifespan shutdown hooks.

## 4. API Endpoints Surface

- [x] 4.1 Create `app/endpoints/auth.py` and define the router for auth management endpoints.
- [x] 4.2 Implement GET `/v1/auth/status` to return current cached state metrics, timestamps, and whether a login is active (`LOGIN_IN_PROGRESS`).
- [x] 4.3 Implement POST `/v1/auth/login` to initiate the on-demand bootstrap and acquire the coordinator lock.
- [x] 4.4 Register the router in `app/main.py` and implement unit tests verifying all status transitions and error responses.

## 5. Docker Headless Validations

- [x] 5.1 Implement host-level display availability checks within the login workflow inside `AuthManager`.
- [x] 5.2 Add fail-fast assertions to raise HTTP 400 when `/v1/auth/login` is triggered in pure headless displayless container environments.
