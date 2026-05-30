# Docker Deployment Model

This document outlines the containerized execution environment, multi-stage runtime configuration, environment-specific orchestration policies, and persistent storage structure for the WebAI-to-API runtime.

> **Status:** Production Hardening  
> **Scope:** Containerization, Environment Orchestration, and Volume Persistence  

---

## 1. Purpose & Scope

The **Docker Deployment Model** provides environment parity across development, testing, and production phases. By encapsulating dependencies, Playwright-native system packages, and web automation drivers inside a standard container runtime, the deployment layer enforces process isolation and provides a clean environment for browser operations.

- **Container Configuration**: Standardizes execution runtime, Python path structures, and logging pipelines.
- **Orchestration Boundaries**: Manages differences between development (hot-reload, source synchronization) and production (detached multi-worker, automated recovery) runtimes.
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

## 3. Orchestration & Environments

The system supports two execution paths governed by the `.env` variable `ENVIRONMENT`.

### 3.1 Development Mode (`ENVIRONMENT=development`)
- **Interactive Execution**: Runs in the foreground, projecting real-time stack traces and console outputs.
- **Compose Watch Synchronization**: When supported by the installed Docker Compose version (v2.24+) and local host setup, `compose watch` syncs local source directories with the container filesystem:
  - `./src` maps to `/app/src`
  - `./app` maps to `/app/app`
  - `./requirements.txt` maps to `/app/requirements.txt`
- **Dynamic Reloading**: Uvicorn monitors mapped paths inside the container and performs restarts on change.

### 3.2 Production Mode (`ENVIRONMENT=production`)
- **Detached Execution**: Executes as a background daemon process (`docker compose up -d`) to prevent execution interruption from terminal closures.
- **Container Restart Policy**: Enforces `restart: always` to automatically recover from unhandled process termination or host reboot.
- **Multi-Worker Execution**: If configured with multiple workers (e.g. by setting `--workers` in the Uvicorn start command or specifying replica counts in the Docker Compose configuration), requests are load-balanced to handle concurrency.

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
| `make up` | `docker compose up` or `docker compose up -d` | Launches the container in interactive mode (if `ENVIRONMENT=development`) or background mode (if `ENVIRONMENT=production`). |
| `make stop` | `docker compose down` | Stops and removes active container instances and associated networks. |
| `make down` | `docker compose down` | Stops and removes container allocations (identical to `make stop`). |

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
To maintain optimal container state, follow these build guidelines:
- **`docker compose up`**: Sufficient for standard daily operations where only Python source files (`.py`) under `src/` or `app/` have changed, and dependencies remain unchanged.
- **`docker compose up --build` or `make build`**: Required whenever there are changes in system packages, the `Dockerfile`, or Python dependencies in `requirements.txt`.
- **`make build-fresh`**: Recommended when troubleshooting package mismatch issues, resetting cached layers, or performing a clean verification of the dependency tree.

### 7.2 Version Alignment
> WARNING:
> The Playwright library version installed via `requirements.txt` (e.g., `playwright==1.52.0`) MUST match the browser driver versions packed inside the base image (`mcr.microsoft.com/playwright/python:v1.52.0-noble`). Mismatches between the library and driver versions can lead to runtime execution failures during browser automation.
