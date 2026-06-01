## 🐳 Docker Deployment Guide

### Prerequisites

Ensure you have the following installed on your system:

- [Docker](https://docs.docker.com/get-docker/)
- [Docker Compose v2.24+](https://docs.docker.com/compose/)
- GNU Make (optional but recommended)

---

### 🛠️ Docker Environment Configuration

This project uses a `.env` file for environment-specific settings like development or production mode on docker.

#### Example `.env`

```env
# Set the environment mode
ENVIRONMENT=development
```

- `ENVIRONMENT=development`: Runs the server in **development** mode with auto-reload and debug logs.
- Change to `ENVIRONMENT=production` to enable **multi-worker production** mode with detached execution (`make up`).

> **Tip:** If this variable is not set, the default is automatically assumed to be `development`.

---

### Build & Run

> Use `make` commands for simplified usage.

#### 🔧 Build the Docker image

```bash
make build         # Regular build
make build-fresh   # Force clean build (no cache)
```

#### Run the server

```bash
make up
```

Depending on the environment:

- In **development**, the server runs in the foreground with hot-reloading.
- In **production**, the server runs in **detached mode** (`-d`) with multiple workers.

#### ⏹ Stop the server

```bash
make stop
```

---

### Development Notes

- **Reloading**: In development, the server uses `uvicorn --reload` for live updates.
- **Logging**: On container start, it prints the current environment with colors (🟡 dev / ⚪ production).
- **Watch Mode (optional)**: Docker Compose v2.24+ supports file watching via the `compose watch` feature. If enabled, press `w` to toggle.

---

### 🔧 Playwright Authentication Setup

For production deployments using the **Playwright backend** (`playwright/*` models), authentication must be configured BEFORE starting the Docker container.

#### Step-by-Step Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Amm1rr/WebAI-to-API.git
   cd WebAI-to-API
   ```

2. **Install Python dependencies on your HOST machine:**
   ```bash
   # Install Poetry if needed: curl -sSL https://install.python-poetry.org | python3 -
   poetry install
   ```

3. **Install Playwright browser binaries on your HOST machine:**
   ```bash
   poetry run playwright install chromium
   ```

4. **Generate authentication state:**
   ```bash
   poetry run python verify_login.py
   ```
   
   A browser window will open. Log in to your Google account. Once the chat interface is visible, press ENTER in the terminal. The script will save authentication state to `runtime/auth/gemini.json`.

5. **Verify authentication state was created:**
   ```bash
   cat runtime/auth/gemini.json
   ```

6. **Build and start the Docker container:**
   ```bash
   make build
   make up
   ```

7. **Verify the container is running with authentication:**
   ```bash
   curl http://localhost:6969/v1/auth/status
   ```

8. **Use Playwright models:**
   ```bash
   curl -X POST http://localhost:6969/v1/chat/completions \
     -H "Content-Type: application/json" \
     -d '{"model": "playwright/gemini-3-flash", "messages": [{"role": "user", "content": "Hello!"}]}'
   ```

**Important Notes:**
- `verify_login.py` MUST run on your HOST machine (not inside Docker)
- The Docker container runs headlessly and cannot open browser windows
- Authentication persists across container restarts via the volume mount
- To refresh authentication, re-run `poetry run python verify_login.py` on the host and restart the container

#### Quick Start with Playwright

For a quick Playwright setup:

```bash
# 1. One-time setup (run on host)
poetry run python verify_login.py

# 2. Start Docker
make up

# 3. Test Playwright endpoint
curl -X POST http://localhost:6969/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "playwright/gemini-3-flash", "messages": [{"role": "user", "content": "Hello!"}]}'
```

**Note:** If `verify_login.py` fails, ensure Playwright dependencies are installed:
```bash
poetry install
poetry run playwright install chromium
```

---

### Frequently Asked Questions

**Q: Where is authentication stored?**

A: Authentication state is stored in `runtime/auth/gemini.json`. This file is created by `verify_login.py` on your host machine and consumed by the Docker container via the `./runtime:/app/runtime` volume mount.

**Q: Does authentication survive container recreation?**

A: Yes, as long as the `./runtime` directory is preserved on your host. The volume mount ensures `runtime/auth/gemini.json` persists across container restarts, rebuilds, and recreation.

**Q: Can I generate authentication after starting the container?**

A: Yes. Run `poetry run python verify_login.py` on your host machine, then restart the container with `make stop && make up`. The updated authentication state is picked up when the Docker container restarts, because Playwright loads runtime/auth/gemini.json only when creating a new browser context.

**Note:** Updating runtime/auth/gemini.json while the container is already running does not hot-reload the active Playwright context. Restart the container after re-running verify_login.py.

---

### File Structure for Docker

Key files:

```plaintext
.
├── Dockerfile              # Base image and command logic
├── docker-compose.yml      # Shared config (network, ports, env)
├── .env                    # Defines ENVIRONMENT (development/production)
├── Makefile                # Simplifies Docker CLI usage
```

Runtime-generated files are persisted through `./runtime:/app/runtime`.

---

### Best Practices

- Don't use `ENVIRONMENT=development` in **production**.
- Avoid bind mounts (`volumes`) in production to ensure image consistency.
