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

### 🚀 Build & Run

> Use `make` commands for simplified usage.

#### 🔧 Build the Docker image

```bash
make build         # Regular build
make build-fresh   # Force clean build (no cache)
```

#### ▶️ Run the server

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

### 🧠 Development Notes

- **Reloading**: In development, the server uses `uvicorn --reload` for live updates.
- **Logging**: On container start, it prints the current environment with colors (🟡 dev / ⚪ production).
- **Watch Mode (optional)**: Docker Compose v2.24+ supports file watching via the `compose watch` feature. If enabled, press `w` to toggle.

---

### 📦 File Structure for Docker

Key files:

```plaintext
.
├── Dockerfile              # Base image and command logic
├── docker-compose.yml      # Shared config (network, ports, env)
├── .env                    # Defines ENVIRONMENT (development/production)
├── Makefile                # Simplifies Docker CLI usage
```

---

### ✅ Best Practices

- Don't use `ENVIRONMENT=development` in **production**.
- Avoid bind mounts (`volumes`) in production to ensure image consistency.
