## Context

Authentication for Gemini within WebAI-to-API consists of the `gemini-webapi` direct HTTP wrapper and the Playwright-driven browser session manager. The periodic token rotation and cookie expiration require periodic re-authentication. The manual bootstrap utility (`verify_login.py`) is headful and out-of-band, creating friction for headless containers.

This design introduces a conservative, API-driven **Authentication Management Layer** (`AuthManager`) that exposes status flags, coordinates on-demand headful sign-ins, and manages auth locks, while strictly respecting the core execution boundaries of the `BrowserEngine` and `ProviderSession`.

---

## Goals / Non-Goals

### Goals
- **Unified Authentication Status Layer**: Introduce `AuthManager` to cache and query auth status for `gemini-webapi` and Playwright sessions.
- **On-Demand REST Login trigger**: Provide a simple, controlled `POST /v1/auth/login` endpoint that initiates a single isolated sign-in browser session under the coordination of `AuthManager`.
- **Active Login Protection**: Implement an async lock inside `AuthManager` to prevent duplicate concurrent logins and return HTTP 503 to standard chat routes *only* during active login triggers.
- **Fail-Fast Expired Requests**: Ensure chat completions fail-fast with a 401 Unauthenticated response on expired credentials *only* when the target provider/request requires authenticated state (e.g. Playwright routes, or stateful `gemini-chat` requests requiring conversation history in `gemini-webapi`).
- **Persistence Boundary Protection**: Enforce read-only state rules on standard request routes, writing `gemini.json` strictly during explicit login workflows.

### Non-Goals
- Remote streaming, noVNC, WebSocket viewport streaming, or VNC integrations (treated strictly as future extension points).
- Injecting login workflow methods (such as `start_login_flow`) or API-level locks inside `BrowserEngine`.
- Introducing third-party OAuth, cookie injection, credentials storage, or CAPTCHA solvers.
- Registering new global OS signal handlers (leveraging the existing lifespan manager in `main.py`).

---

## Decisions

### Decision 1: Authentication Status Layer (`AuthManager`)
We will introduce `AuthManager` as a service class inside `src/app/services/browser/auth_manager.py`. It will own the cached status dictionary:
- **Playwright Statuses**:
  - `VALID_SESSION`: State file `gemini.json` exists, parses correctly, and checks are authenticated.
  - `NO_SESSION`: No state file exists in `auth_state_dir`.
  - `EXPIRED_SESSION`: State file exists, but active checks fail or redirect to sign-in.
  - `INVALID_STATE`: State file is corrupted.
- **`gemini-webapi` Statuses**:
  - `AUTHENTICATED`, `GUEST`, `INVALID` (based on `_gemini_client` account status).
- **Active State Machine**:
  - `IDLE`: No active login session.
  - `LOGIN_IN_PROGRESS`: On-demand login workflow is currently executing.
- **Rationale**: `AuthManager` isolates all auth status logic, ensuring the `BrowserEngine` remains a focused execution engine for browser and session lifecycles.

### Decision 2: Isolated Bootstrap Execution via `BrowserEngine` Primitives
- The `POST /v1/auth/login` endpoint delegates execution to `AuthManager.run_login_flow()`.
- `AuthManager` acquires its internal `login_lock` and transitions state to `LOGIN_IN_PROGRESS`.
- To launch the browser, `AuthManager` uses only the existing browser/session execution primitives of `BrowserEngine` (specifically calling `BrowserEngine.get_browser_engine(headless=False, is_bootstrap=True)` to create an isolated headful process).
- It obtains a managed page lease with `enable_persistence=True`.
- It navigates to `https://gemini.google.com/app` and monitors `SELECTORS["INPUT"]` to detect successful user interaction.
- Once login is confirmed, it calls `session.save_state()` to atomically serialize cookies to `gemini.json`.
- On completion, crash, or timeout, the page lease is released, the isolated engine is cleanly closed, the lock is released, and state returns to `IDLE`.
- **Rationale**: Placing `run_login_flow` completely inside `AuthManager` keeps `BrowserEngine` stateless regarding the active sign-in orchestration, preventing code pollution of the core driver.

### Decision 3: Concurrency Coordination & Locks
To prevent login storms, auth thrashing, and concurrent writes across multi-worker or scaled SaaS environments, `AuthManager` implements the **`AuthCoordinationLock`** abstraction:
1. **`AuthCoordinationLock` Interface**: Defines the `acquire()`, `release()`, and `is_locked()` lock lifecycle contract, allowing the concurrency coordination layer to scale beyond a single Python process in the future.
2. **Process-Bound In-Memory Lock (`InMemoryAuthLock`)**: The default MVP implementation uses a localized `threading.Lock` wrapper. This is thread-safe within a single Python process, but is explicitly documented as **not multi-worker safe** (i.e. it does not protect across multiple Uvicorn/Gunicorn workers or distributed nodes).
3. **Production Scalability Backend (`auth_lock_backend`)**: Exposes a clear configuration boundary (`auth_lock_backend = in_memory` under `[Playwright]`) allowing seamless transition to distributed lock backends (e.g., Redis `SET NX` with TTL, Postgres advisory locks, or database-backed lease rows) for production SaaS or multi-worker environments.
4. **Active Multi-Worker Detection**: If the configuration is set to `in_memory` but multiple workers are detected via environment variables (`WEB_CONCURRENCY` or `WORKERS` > 1), `AuthManager` logs a clear operational warning indicating that in-memory locking is not safe for concurrent workers.
5. **Standard Route Protection**: If a chat request (`/v1/chat/completions`) is processed while the coordination lock is locked (`auth_mgr.coordination_lock.is_locked()`), it fails-fast with HTTP 503 (Service Unavailable: Authentication in progress).
6. **Expired Auth Routing**: If the coordination lock is not active but the status is `EXPIRED_SESSION`, `/v1/chat/completions` immediately returns HTTP 401 (Unauthenticated) *only* if the request requires an authenticated session. Guest-compatible requests (e.g. unauthenticated `gemini-webapi` chat requests without history tracking) are allowed to proceed to support flexible fallbacks.

### Decision 4: Minimal API Surface
We will expose exactly two endpoints under `app.endpoints.auth`:
- `GET /v1/auth/status`: Returns JSON containing current cached state for both pathways, last validated timestamp, and whether a login is currently active (`LOGIN_IN_PROGRESS`).
- `POST /v1/auth/login`: Triggers the on-demand bootstrap and returns HTTP 202 (Accepted).
- **Simplified Progress**: To keep the design extremely conservative, no SSE or WebSocket progress endpoints will be created. Clients can poll the `GET /v1/auth/status` endpoint to check if the status transitions from `LOGIN_IN_PROGRESS` to `VALID_SESSION`. No cancel endpoints are introduced.

### Decision 5: Headless Docker Compatibility
- **Headless Fallback**: If the container is displayless and configured with `headless = True`, triggering `POST /v1/auth/login` checks display availability. If unavailable, it immediately fails-fast with HTTP 400 (Bad Request), stating that headful interactive login is unsupported.
- **Import Pathway**: Production headless containers continue to rely on copying a pre-generated `gemini.json` state file via `auth_state_dir` volume mount.

---

## Risks / Trade-offs

* **[Risk] State corruption during concurrent writes**  
  *Mitigation*: Enforce atomic file serialization (write to tmp -> `fsync` -> rename) inside `ProviderSession.save_state` protected by `state_lock`.
* **[Risk] Zombie Chromium processes on crash**  
  *Mitigation*: Leverage the existing FastAPI server lifespan shutdown in `main.py` which calls `engine.close()`, guaranteeing teardown of all active and isolated engine contexts.
* **[Risk] Overlapping triggers causing multiple windows**  
  *Mitigation*: Ensure `login_lock` in `AuthManager` is acquired before spinning up any browser processes.
