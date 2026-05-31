## Why

Currently, the WebAI-to-API application requires users to manually execute `verify_login.py` out-of-band in headful mode to bootstrap Playwright authentication for Gemini. This creates operational friction, prevents automatic recovery of expired sessions in headless Docker deployments, and lacks programmatic endpoints to query auth status or trigger login workflows on-demand.

## What Changes

- **Authentication Status Layer (`AuthManager`)**: Introduce a centralized, in-memory `AuthManager` that owns the authentication status cache, login coordination locks, and the authentication state machine for both `gemini-webapi` (`AUTHENTICATED`, `GUEST`, `INVALID`) and Playwright (`VALID_SESSION`, `NO_SESSION`, `EXPIRED_SESSION`, `INVALID_STATE`).
- **On-Demand API-Driven Login**: Add a controlled, runtime-triggered login workflow accessible via a simple FastAPI endpoint (`POST /v1/auth/login`) that initiates a single isolated sign-in browser session under the coordination of `AuthManager`.
- **Concurrency & Lock Coordination**: Utilize locks in `AuthManager` to prevent overlapping login triggers, login storms, and concurrent state file writes, while allowing standard API endpoints to fail-fast with HTTP 503 only during active login workflows.
- **Docker Portability**: Provide headless-safe assertions that immediately fail-fast when headful interactive login is triggered in a displayless container, while fully supporting pre-generated state file imports.

## Capabilities

### New Capabilities
- `on-demand-auth-bootstrap`: Centralizes status tracking, coordination locks, the auth state machine, and API endpoints inside `AuthManager`.

### Modified Capabilities
- `docker-compatible-auth`: Refine requirements to explicitly permit state writes *only* inside explicit login/bootstrap workflows, keeping normal request paths strictly read-only.
- `robust-auth-recovery`: Integrate auth status checks to ensure chat completions fail-fast with a 401 unauthenticated response when credentials are expired, returning 503 only during active login triggers.

## Impact

- **Affected Code**: `src/app/services/browser/engine.py`, `src/app/services/browser/session.py`, `src/app/services/gemini_client.py`.
- **New Code**: `src/app/endpoints/auth.py` (FastAPI status and login endpoints), `src/app/services/browser/auth_manager.py` (central coordinator, status manager, and locks).
- **API Surface**: Adds `/v1/auth/status` and `/v1/auth/login`. No progress or cancel endpoints.
