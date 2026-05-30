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
- **Persistent Authentication State**: Mounts `./auth_state` into the container to preserve browser authentication and session data across container restarts and redeployments.

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
- **Persistent session files**: User-visible session profiles, cookies (`gemini.json`), and conversation history are persisted.

### 4.2 Storage Mounts
- **Bind mount configuration**: Maps the local host path `./auth_state` to `/app/auth_state` inside the container.
- **Volume persistence**: When the Playwright session updates or performs autosaving, state files are written within the mounted volume, surviving container recreation.

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

### 6.1 Authentication Verification Inside the Container
Verify browser authentication within the active container:
```bash
docker exec -it web_ai_server python verify_login.py
```

### 6.2 Log Ingestion
Monitor server output, request lifecycles, and session health logs:
```bash
docker logs -f web_ai_server
```

---

## 7. Operational Notes

### 7.1 Image Rebuild
Because the production-only container maps only persistent browser data directories (`./auth_state`) and does not bind-mount source code directories, any modification to Python source files (`.py` under `src/` or `app/`) requires an image rebuild to be projected into the active container runtime:
- **`docker-compose up --build` or `make build`**: Required whenever there are changes to Python source code, system packages, the `Dockerfile`, or Python dependencies in `requirements.txt`.
- **`make build-fresh`**: Recommended when troubleshooting package mismatch issues, resetting cached layers, or performing a clean verification of the dependency tree.

### 7.2 Version Alignment
> WARNING:
> The Playwright library version installed via `requirements.txt` (e.g., `playwright==1.52.0`) MUST match the browser driver versions packed inside the base image (`mcr.microsoft.com/playwright/python:v1.52.0-noble`). Mismatches between the library and driver versions can lead to runtime execution failures during browser automation.
