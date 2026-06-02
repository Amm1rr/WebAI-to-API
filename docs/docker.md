# Docker Deployment Guide

This document describes how to run WebAI-to-API using Docker and how to configure authentication for browser-based providers.

The built-in dashboard under `/ui/*` is also exposed by the service. If you map the service port to a public interface, you expose the dashboard routes as well. See [Dashboard Guide](dashboard.md) for the dashboard security posture and available pages.

## Prerequisites

Required software:

* Docker
* Docker Compose
* GNU Make (optional)

---

## Environment Configuration

Create a `.env` file:

```env
ENVIRONMENT=production
```

Available values:

| Value         | Description      |
| ------------- | ---------------- |
| `development` | Development mode |
| `production`  | Production mode  |

If not specified, the application defaults to development mode.

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

Stop the stack:

```bash
make stop
```

---

## Playwright Authentication

Playwright-based models require browser authentication before the container starts.

Authentication must be generated on the host machine.

### Generate Authentication

Install dependencies:

```bash
poetry install
```

Install Playwright browser binaries:

```bash
poetry run playwright install chromium
```

Run the authentication workflow:

```bash
poetry run python verify_login.py
```

A browser window will open.

1. Sign in to your Google account.
2. Wait until Gemini is accessible.
3. Return to the terminal and complete the workflow.

Authentication state will be stored in:

```text
runtime/auth/gemini.json
```

Verify the file exists:

```bash
ls runtime/auth/gemini.json
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

---

## Authentication Persistence

Authentication is stored in:

```text
runtime/auth/gemini.json
```

The Docker configuration mounts:

```text
./runtime:/app/runtime
```

As long as the `runtime` directory is preserved, authentication survives:

* Container restarts
* Container recreation
* Image rebuilds
* Host reboots

---

## Refreshing Authentication

If authentication expires:

```bash
poetry run python verify_login.py
```

Then restart the container:

```bash
make stop
make up
```

Authentication is loaded when a new Playwright browser context is created.

Updating `runtime/auth/gemini.json` while the container is running does not update existing browser contexts.

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
* Session persistence
* Runtime cache data

For Playwright deployments, preserving this directory is recommended.

---

## File Layout

```text
.
├── Dockerfile
├── docker-compose.yml
├── .env
├── Makefile
└── runtime/
```

---

## Best Practices

* Use `production` mode for deployed environments.
* Generate Playwright authentication on the host machine.
* Preserve the `runtime` directory between deployments.
* Restart containers after refreshing authentication.
* Use health and readiness endpoints for monitoring.
* Do not expose the service port publicly unless you also secure the `/ui/*` dashboard routes with an external access-control layer.
