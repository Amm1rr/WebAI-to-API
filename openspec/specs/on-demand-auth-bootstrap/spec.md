# on-demand-auth-bootstrap Specification

## Purpose
TBD - created by archiving change on-demand-auth-bootstrap. Update Purpose after archive.
## Requirements
### Requirement: Centralized Authentication Status Monitoring
The system SHALL maintain a centralized, thread-safe, and cached authentication status monitor (`AuthManager`) for both the `gemini-webapi` connection and Playwright-driven browser sessions. The system SHALL query status state asynchronously on application startup, cache the results, and refresh the state asynchronously on-demand or during lifecycle recovery triggers to ensure zero latency overhead on normal chat execution paths.

#### Scenario: Querying authentication status
- **WHEN** an API client requests the current authentication status from the endpoint `/v1/auth/status`
- **THEN** the system SHALL return the cached status for both providers without executing active network navigations or browser launches

#### Scenario: Authentication status transitions on invalidation
- **WHEN** a Playwright session fails its pre-submission authentication check
- **THEN** `AuthManager` SHALL immediately transition the cached Playwright status from `VALID_SESSION` to `EXPIRED_SESSION`
- **AND** the `gemini-webapi` status SHALL be refreshed to check if it has fallen back to guest mode

---

### Requirement: On-Demand API-Driven Login Workflow
The system SHALL provide an API-driven, controlled login workflow that replaces manual out-of-band utility executions. The `AuthManager` SHALL own and orchestrate the login workflow via `AuthManager.run_login_flow()`. When triggered, `AuthManager` SHALL use the existing browser/session primitives of `BrowserEngine` to launch Chromium in a controlled, isolated headful process (when local), navigate to the Gemini app, and monitor for user authentication. The workflow MUST preserve all existing runtime contracts.

#### Scenario: Successful on-demand login completion
- **WHEN** an administrator triggers the login endpoint `/v1/auth/login` and completes Google sign-in in the browser window
- **THEN** `AuthManager` SHALL detect the chat input box, invoke atomic persistence to save `gemini.json`, transition Playwright status to `VALID_SESSION`, and close the login page lease
- **AND** the system SHALL NOT crash or leave zombie browser processes

#### Scenario: User cancels login by closing window
- **WHEN** the browser window is closed manually by the user during the active login workflow
- **THEN** the workflow SHALL catch the closure event, transition the status to `EXPIRED_SESSION`, and release all active page leases and locks safely

---

### Requirement: Authentication Concurrency Coordination
The `AuthManager` SHALL implement the `AuthCoordinationLock` abstraction to coordinate active logins. The system SHALL support process-bound in-memory locking (`InMemoryAuthLock`) as the default, and SHALL support swapping the backend via configuration (`auth_lock_backend = in_memory` under `[Playwright]`) to a distributed lock backend (such as Redis or Postgres) in multi-worker or scaled SaaS environments.

#### Scenario: Concurrent login requests debounced via coordination lock
- **WHEN** multiple concurrent API requests trigger the login workflow simultaneously
- **THEN** `AuthManager` SHALL check if `AuthCoordinationLock` is acquired
- **AND** the system SHALL fail subsequent concurrent triggers immediately with a conflict message (HTTP 409)

#### Scenario: Multi-worker warning when using in-memory lock
- **WHEN** `AuthManager` is initialized with `auth_lock_backend = in_memory`
- **AND** multiple workers are detected via the environment variables `WEB_CONCURRENCY` or `WORKERS` (value > 1)
- **THEN** `AuthManager` SHALL log a clear warning explaining that `InMemoryAuthLock` is not multi-worker safe and that a distributed backend must be used in production

#### Scenario: Normal chat requests blocked only during active login lock
- **WHEN** a normal chat request `/v1/chat/completions` arrives while `AuthCoordinationLock` is locked
- **THEN** the system SHALL fail-fast the chat request immediately with an authentication-in-progress message (HTTP 503)

#### Scenario: Chat requests fail-fast on expired auth only when authenticated state is required
- **WHEN** a chat request `/v1/chat/completions` arrives while `AuthCoordinationLock` is not active and Playwright status is `EXPIRED_SESSION`
- **AND** the request target provider/configuration requires an authenticated state (e.g. Playwright provider, or stateful `gemini-chat` requests requiring conversation history continuation)
- **THEN** the system SHALL immediately reject the request with an unauthenticated message (HTTP 401)
- **BUT WHEN** the request target allows an unauthenticated guest session (e.g. standard `gemini-webapi` chat request without history tracking)
- **THEN** the system SHALL allow the request to proceed in guest-mode fallback

