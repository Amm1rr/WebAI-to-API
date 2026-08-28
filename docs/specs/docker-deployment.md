# Docker Deployment Model

This document outlines the containerized execution environment, production-only orchestration policies, and persistent storage structure for the WebAI-to-API runtime.

> **Status:** Production Hardening  
> **Scope:** Containerization, Environment Orchestration, and Volume Persistence  

> For user-facing Docker setup instructions, see [Docker Guide](../docker.md).

---

## 1. Purpose & Scope

The **Docker Deployment Model** provides environment parity across development, testing, and production phases. By encapsulating dependencies, Playwright-native system packages, and web automation drivers inside a standard container runtime, the deployment layer enforces process isolation and provides a clean environment for browser operations.

- **Container Configuration**: Standardizes execution runtime, Python path structures, and logging pipelines.
- **Orchestration Boundaries**: Manages production-hardened process execution, port exposures, and automatic recovery boundaries.
- **Persistence Policies**: Standardizes volume mounts to ensure browser authentication storage state and conversation snapshots survive container lifecycles.

---

## 2. Container Environment Configuration

The containerized environment operates under defined technical constraints to ensure predictable and consistent automation.

### 2.1 Base Operating System & Driver Packages
- **Base Image**: Uses the Playwright-native standard image `mcr.microsoft.com/playwright/python:v1.62.0-noble`.
- **Pre-configured Drivers**: Contains system-level dependencies for running headless Chromium processes without needing runtime package downloads.

### 2.2 System & Python Environment Variables
- **`PYTHONUNBUFFERED=1`**: Forces stdout and stderr streams to be unbuffered. This guarantees real-time log ingestion by Docker/system daemons without buffering delays.
- **`PYTHONPATH=/app/src`**: Registers the server source code directory into Python's `sys.path`, ensuring standard import resolution across all modules.
- **`PLAYWRIGHT_HEADLESS=true`**: Enforces headless operation for browser runtimes inside headless server environments.
- **Non-root execution**: The application runs as Playwright's `pwuser`; build arguments `APP_UID` and `APP_GID` default to `1000:1000` and control its numeric IDs.

---

## 3. Orchestration & Production Execution

The WebAI-to-API server is orchestrated strictly for production execution using Docker Compose.

### 3.1 Production Service Configuration

The service is defined in `docker-compose.yml` for production execution:

- **Detached Execution**: The service is typically run using `docker compose up -d` to prevent interruption from terminal closures.
- **Container Restart Policy**: Enforces `restart: always` to automatically recover from process crashes or host reboots.
- **Port Exposure**: Publishes host port `${WEB_PORT:-6969}` to the fixed container application port `6969` on `127.0.0.1` by default. Setting `WEB_PORT=8080` makes the service available at `http://127.0.0.1:8080`; the application continues listening on container port `6969`.
- **Host Interface Exposure**: `${DOCKER_BIND_ADDRESS:-127.0.0.1}` controls the published host interface. Set `DOCKER_BIND_ADDRESS=0.0.0.0` for explicit LAN/server access. The previous default published on all host interfaces.
- **Caller Authentication**: The project provides no caller API authentication. Broader publication requires external authentication in front of the entire service, including API and dashboard routes.
- **Environment Configuration**: Loads variables from `.env` and applies container runtime settings such as `PYTHONPATH` and `PLAYWRIGHT_HEADLESS`. Compose fixes application path variables to the container runtime root.
- **UID/GID Contract**: Native Linux deployments should build with `APP_UID` and `APP_GID` matching the host user that owns the Docker runtime source.
- **Persistent Runtime State**: Mounts `./config.conf` (read-only) and `${DOCKER_RUNTIME_DIR:-./runtime}` at `/app/runtime`. `DOCKER_RUNTIME_DIR` selects the host source; application paths inside Docker remain fixed. Application logs are emitted to stdout/stderr.

### 3.2 Runtime Topology

The current deployment model operates in a single-worker configuration:

- **Single Process Topology**: Uvicorn runs with `--workers 1`.
- **No Dynamic Reloading**: The container runs without source watching or `--reload` mode.
- **Static Container Image**: Application source code is baked into the image at build time and is not bind-mounted into the running container.

---

## 4. Storage & Session Persistence

Browser authentication storage state and local conversation snapshots are
persisted through mounted volumes, ensuring they survive container
recreation, redeployments, and normal container restarts.

### 4.1 Ephemeral vs. Persistent Boundaries
- **Ephemeral assets**: Source files and dependencies are stored in image layers. The Gemini WebAPI dependency cookie cache is lifecycle-scoped system temporary state, not application state persisted by the `runtime` bind mount.
- **Persistent runtime files**:
  - **`config.conf`**: Application settings (mounted read-only).
  - **`/app/runtime/auth/gemini.json`**: Playwright storage state.
  - **`/app/runtime/conversations/`**: SQLite conversation snapshots.
  - **`/app/runtime/cache/`**: Bootstrap-created runtime directory layout; not the Gemini WebAPI dependency cookie cache.
  - **Logs**: Application logs are streamed to stdout/stderr.

The Gemini WebAPI dependency cookie cache uses an application-owned random
directory in system temporary storage. It is lifecycle-scoped, shared by
active and retired generations in one lifecycle, cleaned after generations and
leases drain, and removed after completely failed initialization when unused.
The runtime restores any previous `GEMINI_COOKIE_PATH` value. POSIX cache
directories are private; Windows relies on the supported Python patch floor.

### 4.2 Storage Mounts
- **Bind mount configuration**:
  - Maps the local host file `./config.conf` to `/app/config.conf` (read-only).
  - Maps the local host path `${DOCKER_RUNTIME_DIR:-./runtime}` to `/app/runtime`.
- **Host file precondition**: Compose does not create missing bind sources; `config.conf` and the effective Docker runtime source must exist, while `.env` is required by `env_file`.
- **Host preflight**: The canonical `make up` and `make up-attach` targets require the effective Docker runtime source to exist before Docker starts, preventing daemon-created root-owned bind sources.
- **Volume persistence**: Authentication state and conversation snapshots are written within the mounted volume, surviving container recreation.

The Docker conversation database is fixed to
`/app/runtime/conversations/conversation_snapshots.db`; native
`CONVERSATION_SNAPSHOT_DB` overrides do not apply in Docker. Docker likewise
fixes `RUNTIME_DIR` and `AUTH_STATE_DIR` to `/app/runtime` and
`/app/runtime/auth`; independent Docker auth/database mounts are unsupported.
Snapshot writes use SQLite WAL mode and
`synchronous=FULL`. On POSIX, the project-owned database and existing SQLite
sidecars are private (`0600`). Custom existing database parent directories are
not forcibly changed to `0700`; Windows uses operating-system ACL behavior
instead of POSIX mode bits.

---

## 5. Operational Tasks

The included `Makefile` provides operational targets for managing the container lifecycle:

| Command | Operation | Details |
| :--- | :--- | :--- |
| `make build` | `docker build` with `APP_UID`/`APP_GID` build arguments | Builds the local Docker image using the default cache. |
| `make build-fresh` | `docker build --no-cache` with `APP_UID`/`APP_GID` build arguments | Rebuilds the container from scratch, ignoring cached layers. |
| `make up` | `docker compose up -d` | Launches the container in detached mode using the project's Docker Compose configuration. |
| `make up-attach` | `docker compose up` | Launches the container in the foreground and streams logs to the terminal. |
| `make logs` | `docker compose logs -f web_ai` | Follows logs from the running `web_ai` service. |
| `make stop` | `docker compose down` | Stops and removes active container instances and associated networks. |
| `make down` | `docker compose down` | Stops and removes container allocations (identical to `make stop`). |

---

## 6. Verification & Monitoring

To verify container state and session authorization:

### 6.1 Playwright Authentication for Production

The Docker process can start without pre-generated Playwright authentication
state. The Playwright backend requires a valid persisted storage-state candidate
before authenticated browser operations. The browser-based login flow requires
a display environment and must be run on the HOST machine, not inside the
Docker container.

**Production Authentication Workflow:**

1. On your HOST machine, run the bootstrap login utility:
   ```bash
   RUNTIME_DIR=runtime AUTH_STATE_DIR=runtime/auth poetry run python verify_login.py
   ```

   For `DOCKER_RUNTIME_DIR=/srv/webai/runtime`, run:
   ```bash
   RUNTIME_DIR=/srv/webai/runtime \
   AUTH_STATE_DIR=/srv/webai/runtime/auth \
   poetry run python verify_login.py
   ```
   Explicit `AUTH_STATE_DIR` prevents native auth overrides from redirecting
   Docker's `auth/gemini.json` state.

2. Complete the Google sign-in process in the browser window that opens.

3. Verify the authentication state file was created:
   ```bash
    ls "${DOCKER_RUNTIME_DIR:-./runtime}/auth/gemini.json"
   ```

4. Start or restart the Docker container (it will consume the auth state via volume mount):
   ```bash
   make up
   ```

The `${DOCKER_RUNTIME_DIR:-./runtime}:/app/runtime` volume mount ensures the
host source's `auth/gemini.json` is available at `/app/runtime/auth/gemini.json`.
Docker fixes this application path; custom Docker auth paths are unsupported.
For a custom source, run host login with both native paths set to that source:
```bash
RUNTIME_DIR=/srv/webai/runtime \
AUTH_STATE_DIR=/srv/webai/runtime/auth \
poetry run python verify_login.py
```

**Note:** The `/v1/auth/login` endpoint is NOT supported in Docker deployments because it requires a headful display environment.

### 6.2 Understanding Authentication Methods

WebAI-to-API supports two distinct authentication approaches:

**Gemini WebAPI Backend:**
- Uses unofficial API wrappers
- Authenticates via cookies (`__Secure-1PSID`, `__Secure-1PSIDTS`)
- Cookies configured in `config.conf` [Gemini] section
- No browser required
- Works immediately in Docker

**Gemini Playwright Backend:**
- Drives real Chromium browser via Playwright
- Requires `/app/runtime/auth/gemini.json` storage state in Docker
- State file generated by `verify_login.py` on HOST machine
- Docker container consumes state file via volume mount
- Provides maximum resilience against web UI changes

**Authentication State File (`/app/runtime/auth/gemini.json` in Docker):**

This file contains Playwright `storageState` data including:
- Google authentication cookies
- LocalStorage data
- Origin permissions

The file is created on the HOST machine under the selected Docker source and
consumed through `${DOCKER_RUNTIME_DIR:-./runtime}:/app/runtime`.

**Login Endpoint Limitations:**

The `/v1/auth/login` API endpoint opens a browser window and requires a display environment. In Docker deployments:
- The container runs headlessly (`PLAYWRIGHT_HEADLESS=true`)
- No display server is available inside the container
- Therefore, `/v1/auth/login` will fail with: "Headful interactive sign-in is unsupported in this headless container environment"

For Docker + Playwright authentication, always use `verify_login.py` on the host machine as documented above.

### 6.3 Log Ingestion
Monitor server output, request lifecycles, and session health logs:
```bash
docker logs -f web_ai_server
```

---

## 7. Operational Notes

### 7.1 Image Rebuild
Because the production-only container maps only its selected persistent runtime source and does not bind-mount source code directories, any modification to Python source files (`.py` under `src/` or `app/`) requires an image rebuild to be projected into the active container runtime:
- **`docker compose up --build` or `make build`**: Required whenever there are changes to Python source code, system packages, the `Dockerfile`, or Python dependencies in `requirements.txt`.
- **`make build-fresh`**: Recommended when troubleshooting package mismatch issues, resetting cached layers, or performing a clean verification of the dependency tree.

### 7.2 Version Alignment
> WARNING:
> The Playwright library version installed via `requirements.txt` (e.g., `playwright==1.62.0`) MUST match the browser driver versions packed inside the base image (`mcr.microsoft.com/playwright/python:v1.62.0-noble`). Mismatches between the library and driver versions can lead to runtime execution failures during browser automation.

### 7.3 Frequently Asked Questions

**Q: Where is authentication stored?**

A: Docker authentication state is stored at `auth/gemini.json` under the
selected `DOCKER_RUNTIME_DIR` host source. The container reads it at
`/app/runtime/auth/gemini.json`.

**Q: Does authentication survive container recreation?**

A: Yes. Because `auth/gemini.json` is stored in the selected Docker runtime
source on the host (not inside the container), authentication
persists across:
- Container restarts (`docker compose restart`)
- Container recreation (`docker compose down && docker compose up -d`)
- Image rebuilds (`make build`)

Authentication is only lost if that host auth state is deleted or its Docker
mount is removed.

**Q: Can I generate authentication after starting the container?**

A: Yes. Re-run the documented host login command with both `RUNTIME_DIR` and
`AUTH_STATE_DIR` set to the Docker source, then restart the container with
`make stop && make up`. The updated authentication
state is picked up when the Docker container restarts, because Playwright loads
the configured auth state only when creating a new browser context.

**Note:** Updating the configured auth state while the container is already
running does not hot-reload the active Playwright context. Restart the
container after re-running `verify_login.py`.
