# Docker Deployment Guide

This document describes how to run WebAI-to-API using Docker and how to configure authentication for browser-based providers.

The service has no caller API authentication. The built-in dashboard under
`/ui/*` is also exposed by the service. If you map the service port to a public
interface, you expose the entire API and dashboard. See [Dashboard Guide](dashboard.md)
for the dashboard security posture and available pages.

## Prerequisites

Required software:

* Docker
* Docker Compose
* GNU Make (optional)

Host Python `>=3.11,<3.13` and Poetry are also required when using the
bootstrap utility or host Playwright login. On Windows, use Python `3.11.10+`
or `3.12.4+` for Gemini WebAPI temporary cookie-cache isolation.

---

## Environment Configuration

WebAI-to-API uses two primary configuration files:

1. **`.env`**: Used by Docker Compose to set environment variables (e.g., `ATLASCLOUD_API_KEY`).
2. **`config.conf`**: Used by the application for detailed settings (e.g., Gemini backend, proxy).

### Create Configuration

Run the bootstrap utility to create default configurations:

```bash
python scripts/bootstrap.py
```

Alternatively, copy the examples manually. On POSIX hosts, apply private modes:

```bash
cp .env.example .env
cp config.conf.example config.conf
mkdir -p runtime
chmod 600 .env config.conf
chmod 700 runtime
```

**Note:** `config.conf` is mounted read-only into the container and `.env` is loaded by Docker Compose via `env_file`. `config.conf`, `.env`, and the Docker runtime source must exist on the host before starting the container; Compose rejects missing bind-mount sources. The canonical setup is `python scripts/bootstrap.py`, which creates this state without overwriting existing configuration.

The `chmod` commands are POSIX-specific. Windows users should prefer
`.\install.ps1`, which selects a supported Python interpreter, and use normal
Windows filesystem ACL behavior.

Changes to `config.conf` or `.env` on the host are reflected after the
container is restarted or recreated as applicable; an image rebuild is not
required for configuration-only updates. Do NOT commit `config.conf` or `.env`
as they may contain secrets.

### Host Binding and Port

Docker Compose publishes host port `6969` to the container's fixed application
port `6969` on `127.0.0.1` by default. Access it at
`http://127.0.0.1:6969` or `http://localhost:6969`.

`DOCKER_BIND_ADDRESS` controls the host interface used for Docker port
publication. The safe local default is `127.0.0.1`. Set it explicitly for LAN
or server access:

```env
DOCKER_BIND_ADDRESS=0.0.0.0
```

Previous behavior published on all host interfaces. The new default publishes
on IPv4 loopback only. `127.0.0.1` is an IPv4 bind; no dual-stack mapping is
configured.

To use a different host port, set the optional `WEB_PORT` value in `.env`:

```env
WEB_PORT=8080
```

Then access the service at `http://127.0.0.1:8080`. This changes only the
host-facing port; the application still listens on container port `6969`, and
no application configuration change is required.

`DOCKER_BIND_ADDRESS=0.0.0.0` with or without `WEB_PORT` makes the service
reachable from other machines when routing and host controls allow it. Do not
expose the service to an untrusted network without external authentication in
front of the entire service; the project does not provide caller API
authentication.

The commands below use the default host port `6969`. If `WEB_PORT` is set,
replace `6969` with its configured value; the container port remains `6969`.

### Container UID/GID

The image runs the application as Playwright's non-root `pwuser`. `APP_UID` and
`APP_GID` control that user's numeric IDs and default to `1000:1000`. On native
Linux, match them to the host user that owns the selected Docker runtime source
before building:

```bash
APP_UID=$(id -u) APP_GID=$(id -g) make build
```

Changing these values requires an image rebuild. Do not use `chown -R` or broad
permissions on that source; the container must use the host user's existing
private ownership and modes.

On POSIX systems, bootstrap and runtime storage harden project-owned runtime
directories to `0700` and sensitive files to `0600`, including auth state,
conversation databases, SQLite sidecars, and applicable shutdown metadata.
Windows uses inherited and CPython-created ACLs instead; the project does not
implement custom ACL manipulation. These protections do not protect against
root or administrator access. Do not use `chmod 777`.

`make up` and `make up-attach` require the effective Docker runtime source to already exist. Run
`python scripts/bootstrap.py` first rather than allowing Docker to create the
bind-mount source directory.

`.env`, `config.conf`, and the Docker runtime source are user-owned local state. They are not
updated by the repository; keep them in place when pulling or rebuilding.

### Container Logging Controls

Container logging is configured via environment variables passed into the service. By default, the container logs at `INFO` level and outputs web request access logs to stderr.

You can override these behaviors by passing environment variables:

* **Default Run** (INFO level logs, access logs enabled):
  ```bash
  docker compose up
  ```
* **Enable Container DEBUG Logs**:
  ```bash
  LOG_LEVEL=DEBUG docker compose up
  ```
* **Disable HTTP Request Access Logs**:
  ```bash
  DISABLE_ACCESS_LOGS=true docker compose up
  ```

---

## Build

Build the Docker image:

```bash
make build
```

Force a clean rebuild:

```bash
make build-fresh
```

---

## Run

Start the stack:

```bash
make up
```

Start the stack in the foreground:

```bash
make up-attach
```

Follow container logs from an already running stack:

```bash
make logs
```

Stop the stack:

```bash
make stop
```

---

## Playwright Authentication

The Docker process may start without a Playwright auth JSON. Playwright-based
authenticated operations require a valid persisted storage-state candidate.

Authentication must be generated on the host machine.

### Generate Authentication

Install dependencies and prepare the environment:

```bash
python scripts/bootstrap.py
```

*Note: You can also use `make setup` as a shortcut.*

Run the authentication workflow:

```bash
RUNTIME_DIR=runtime AUTH_STATE_DIR=runtime/auth poetry run python verify_login.py
```

A browser window will open.

1. Sign in to your Google account.
2. Wait until Gemini is accessible.
3. Return to the terminal and complete the workflow.

With the default Docker source, authentication state is stored in:

```text
runtime/auth/gemini.json
```

Docker fixes its container path to `/app/runtime/auth/gemini.json`. For a
custom Docker source, set both native paths to the selected host source:

```bash
RUNTIME_DIR=/srv/webai/runtime \
AUTH_STATE_DIR=/srv/webai/runtime/auth \
poetry run python verify_login.py
```

Docker expects `auth/gemini.json` under its selected runtime source. Explicit
`AUTH_STATE_DIR` prevents native environment or config overrides from redirecting login state elsewhere.

Verify the file exists:

```bash
ls "${DOCKER_RUNTIME_DIR:-./runtime}/auth/gemini.json"
```

---

## Start Docker

After authentication has been generated:

```bash
make build
make up
```

Verify authentication status:

```bash
curl http://localhost:6969/v1/auth/status
```

Use the configured `WEB_PORT` instead of `6969` when a custom host port is set.

---

## Using Playwright Models

Example request:

```bash
curl -X POST http://localhost:6969/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "playwright/gemini-3-flash",
    "messages": [
      {
        "role": "user",
        "content": "Hello!"
      }
    ]
  }'
```

Gemini WebAPI and Playwright support request-scoped Extended Thinking through `provider_options.gemini.extended_thinking`; see [API Documentation](api.md) for request format and semantics.

---

## Configuration & Persistence

The Docker configuration uses bind mounts to persist data and load settings:

```text
./config.conf -> /app/config.conf (read-only bind mount)
${DOCKER_RUNTIME_DIR:-./runtime} -> /app/runtime (read-write bind mount)
```

### 1. Configuration (`config.conf`)
The `config.conf` file is mounted **read-only**. It contains your backend selections, manual cookies, and engine tuning. Because it is mounted at runtime, it is NOT baked into the Docker image, ensuring your secrets remain on your host machine.

### 2. Authentication State
Docker always stores authentication at `/app/runtime/auth/gemini.json`, backed
by `DOCKER_RUNTIME_DIR/auth/gemini.json` on the host. Generate host Playwright
authentication in that selected directory.

### 3. Lifecycle
As long as these files/directories are preserved on the host, configuration and authentication survive:

* Container restarts
* Container recreation
* Image rebuilds
* Host reboots

---

## Refreshing Authentication

If authentication expires:

```bash
RUNTIME_DIR=runtime AUTH_STATE_DIR=runtime/auth poetry run python verify_login.py
```

Then restart the container:

```bash
make stop
make up
```

Authentication is loaded when a new Playwright browser context is created.

Updating the configured auth state file while the container is running does not
update existing browser contexts.

---

## Frequently Asked Questions

### Can authentication be generated inside Docker?

No.

The login workflow requires an interactive browser and must be performed on the host machine.

---

### Does authentication survive container recreation?

Yes.

Authentication is persisted through the mounted `runtime` directory.

---

### Can authentication be refreshed without restarting Docker?

No.

After generating a new authentication state, restart the container so a new browser context can be created.

---

## Runtime Persistence

The `runtime` directory stores persistent runtime state, including:

* Authentication state
* Gemini WebAPI conversation snapshots
* Windows shutdown metadata where applicable

`runtime/cache/` is created by bootstrap as part of the runtime directory
layout. It is not the Gemini WebAPI dependency cookie cache. For Playwright
deployments, preserving the `runtime` directory is recommended.

### Docker Runtime Source

`DOCKER_RUNTIME_DIR` selects only the host bind source, defaulting to
`./runtime`:

```env
DOCKER_RUNTIME_DIR=/srv/webai/runtime
```

That host directory is mounted at `/app/runtime`. Inside Docker, application
paths are always `/app/runtime`, `/app/runtime/auth`, and
`/app/runtime/conversations/conversation_snapshots.db`. Native `RUNTIME_DIR`,
`AUTH_STATE_DIR`, and `CONVERSATION_SNAPSHOT_DB` remain native-only controls;
Compose overrides them even when `.env` sets them. Migrate a prior Docker
`RUNTIME_DIR=/srv/webai/runtime` setting to `DOCKER_RUNTIME_DIR=/srv/webai/runtime`.

Independent Docker auth or conversation database mounts are unsupported. State
below the selected source survives container recreation. Bootstrap creates and
hardens a source it creates; ownership of an existing external source must
allow `APP_UID` and `APP_GID`. Docker fails rather than creating a missing
source as root. Docker Desktop absolute paths may require filesystem-sharing
permission.

## Gemini WebAPI Temporary Cache

Gemini WebAPI dependency cookie data uses an application-owned random
temporary directory in system temporary storage. It is not canonical auth
storage and is not stored in `runtime/auth/` or `runtime/cache/`.

The directory is scoped to the active application lifecycle. Active and
retired Gemini generations in that lifecycle share it; final cleanup waits for
generations and leases to drain. Completely failed initialization removes an
unused cache. The runtime restores any previous `GEMINI_COOKIE_PATH` value
after cleanup.

On POSIX, the cache directory is private. On Windows, the supported Python
patch floor above is required because temporary-directory ACL behavior is
security-sensitive.

---

## File Layout

```text
.
├── Dockerfile
├── docker-compose.yml
├── .env
├── config.conf
├── Makefile
└── runtime/
```

---

## Best Practices

* Generate Playwright authentication on the host machine.
* Preserve the `runtime` directory between deployments.
* Restart containers after refreshing authentication.
* Use health and readiness endpoints for monitoring.
* Keep the default localhost bind, or secure the entire unauthenticated service with external access control before broader exposure.
