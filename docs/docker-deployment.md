# Docker Deployment Model

This document outlines the containerized execution environment, production-only orchestration policies, and persistent storage structure for the WebAI-to-API runtime.

> **Status:** Production Hardening  
> **Scope:** Containerization, Environment Orchestration, and Volume Persistence  

---

## 1. Purpose & Scope

The **Docker Deployment Model** provides environment parity across development, testing, and production phases. By encapsulating dependencies, Playwright-native system packages, and web automation drivers inside a standard container runtime, the deployment layer enforces process isolation and provides a clean environment for browser operations.

- **Container Configuration**: Standardizes execution runtime, Python path structures, and logging pipelines.
- **Orchestration Boundaries**: Manages production-hardened process execution, port exposures, and automatic recovery boundaries.
- **Persistence Policies**: Standardizes volume mounts to ensure browser session profiles survive container lifecycles.

---

## 2. Container Environment Configuration

The containerized environment operates under defined technical constraints to ensure predictable and consistent automation.

### 2.1 Base Operating System & Driver Packages
- **Base Image**: Uses the Playwright-native standard image `mcr.microsoft.com/playwright/python:v1.52.0-noble`.
- **Pre-configured Drivers**: Contains system-level dependencies for running headless Chromium processes without needing runtime package downloads.

### 2.2 System & Python Environment Variables
- **`PYTHONUNBUFFERED=1`**: Forces stdout and stderr streams to be unbuffered. This guarantees real-time log ingestion by Docker/system daemons without buffering delays.
- **`PYTHONPATH=/app/src`**: Registers the server source code directory into Python's `sys.path`, ensuring standard import resolution across all modules.
- **`PLAYWRIGHT_HEADLESS=true`**: Enforces headless operation for browser runtimes inside headless server environments.

---

## 3. Orchestration & Production Execution

The WebAI-to-API server is orchestrated strictly for production execution using Docker Compose.

### 3.1 Production Service Configuration

The service is defined in `docker-compose.yml` for production execution:

- **Detached Execution**: The service is typically run using `docker-compose up -d` to prevent interruption from terminal closures.
- **Container Restart Policy**: Enforces `restart: always` to automatically recover from process crashes or host reboots.
- **Port Exposure**: Maps host port `6969` to container port `6969`.
- **Environment Configuration**: Loads variables from `.env` and applies container runtime settings such as `PYTHONPATH`, `ENVIRONMENT`, and `PLAYWRIGHT_HEADLESS`.
- **Persistent Runtime State**: Mounts `./runtime` into the container to preserve browser authentication state, conversation snapshots, and runtime-generated cache/log directories across container restarts and redeployments.

### 3.2 Runtime Topology

The current deployment model operates in a single-worker configuration:

- **Single Process Topology**: Uvicorn runs with `--workers 1`.
- **No Dynamic Reloading**: The container runs without source watching or `--reload` mode.
- **Static Container Image**: Application source code is baked into the image at build time and is not bind-mounted into the running container.

---

## 4. Storage & Session Persistence

Browser session data is persisted through mounted volumes, ensuring it survives container rollbacks, redeployments, and normal container restarts.

### 4.1 Ephemeral vs. Persistent Boundaries
- **Ephemeral assets**: Source files, dependencies, and internal Playwright page caches are stored in transient container layers and discarded on container rebuilds.
- **Persistent runtime files**: User-visible session profiles, cookies (`runtime/auth/gemini.json`), SQLite conversation snapshots, and runtime-generated logs/cache are persisted.

### 4.2 Storage Mounts
- **Bind mount configuration**: Maps the local host path `./runtime` to `/app/runtime` inside the container.
- **Volume persistence**: Runtime-generated state files are written within the mounted volume, surviving container recreation.

---

## 5. Operational Tasks

The included `Makefile` provides operational targets for managing the container lifecycle:

| Command | Operation | Details |
| :--- | :--- | :--- |
| `make build` | `docker build -t cornatul/webai.ai:latest .` | Builds the local Docker image using the default cache. |
| `make build-fresh` | `docker build --no-cache -t cornatul/webai.ai:latest .` | Rebuilds the container from scratch, ignoring cached layers. |
| `make up` | Docker Compose launch | Launches the production container using the project's Docker Compose configuration. |
| `make stop` | `docker-compose down` | Stops and removes active container instances and associated networks. |
| `make down` | `docker-compose down` | Stops and removes container allocations (identical to `make stop`). |

---

## 6. Verification & Monitoring

To verify container state and session authorization:

### 6.1 Playwright Authentication for Production

The Playwright backend requires pre-generated authentication state for Docker deployments. The browser-based login flow requires a display environment and must be run on the HOST machine, not inside the Docker container.

**Production Authentication Workflow:**

1. On your HOST machine, run the bootstrap login utility:
   ```bash
   poetry run python verify_login.py
   ```

2. Complete the Google sign-in process in the browser window that opens.

3. Verify the authentication state file was created:
   ```bash
   ls runtime/auth/gemini.json
   ```

4. Start the Docker container (it will consume the auth state via volume mount):
   ```bash
   make up
   ```

The `./runtime:/app/runtime` volume mount ensures `runtime/auth/gemini.json` is available inside the container at `/app/runtime/auth/gemini.json`, where the Playwright context automatically loads the authentication cookies.

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
- Requires `runtime/auth/gemini.json` authentication state file
- State file generated by `verify_login.py` on HOST machine
- Docker container consumes state file via volume mount
- Provides maximum resilience against web UI changes

**Authentication State File (`runtime/auth/gemini.json`):**

This file contains Playwright `storageState` data including:
- Google authentication cookies
- LocalStorage data
- Origin permissions

The file is created on the HOST machine by `verify_login.py` and consumed by the Docker container via the `./runtime:/app/runtime` volume mount defined in `docker-compose.yml`.

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
Because the production-only container maps only persistent runtime state directories (`./runtime`) and does not bind-mount source code directories, any modification to Python source files (`.py` under `src/` or `app/`) requires an image rebuild to be projected into the active container runtime:
- **`docker-compose up --build` or `make build`**: Required whenever there are changes to Python source code, system packages, the `Dockerfile`, or Python dependencies in `requirements.txt`.
- **`make build-fresh`**: Recommended when troubleshooting package mismatch issues, resetting cached layers, or performing a clean verification of the dependency tree.

### 7.2 Version Alignment
> WARNING:
> The Playwright library version installed via `requirements.txt` (e.g., `playwright==1.52.0`) MUST match the browser driver versions packed inside the base image (`mcr.microsoft.com/playwright/python:v1.52.0-noble`). Mismatches between the library and driver versions can lead to runtime execution failures during browser automation.

### 7.3 Frequently Asked Questions

**Q: Where is authentication stored?**

A: Authentication state is stored in `runtime/auth/gemini.json` on the host machine. The Docker container accesses this file via the `./runtime:/app/runtime` volume mount defined in `docker-compose.yml`.

**Q: Does authentication survive container recreation?**

A: Yes. Because `runtime/auth/gemini.json` is stored in the `./runtime` directory on the host (not inside the container), authentication persists across:
- Container restarts (`docker-compose restart`)
- Container recreation (`docker-compose down && docker-compose up -d`)
- Image rebuilds (`make build`)

Authentication is only lost if the `./runtime` directory is deleted from the host machine.

**Q: Can I generate authentication after starting the container?**

A: Yes. Run `poetry run python verify_login.py` on your host machine, then restart the container with `make stop && make up`. The updated authentication state is picked up when the Docker container restarts, because Playwright loads runtime/auth/gemini.json only when creating a new browser context.

**Note:** Updating runtime/auth/gemini.json while the container is already running does not hot-reload the active Playwright context. Restart the container after re-running verify_login.py.
