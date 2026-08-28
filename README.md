# WebAI-to-API

<p align="center">
  <img src="./assets/Server-Run-WebAI.png" alt="WebAI-to-API Server" height="160" />
  <img src="./assets/Dashboard.png" alt="Dashboard" height="160" />
  <a href="https://www.atlascloud.ai/?utm_source=github&utm_medium=link&utm_campaign=WebAI-to-API">
    <img src="./assets/ATLAS_CLOUD_LOGO_BLACK.png" alt="Atlas Cloud" height="160" />
  </a>
</p>

**WebAI-to-API** is a browser-native AI runtime that exposes browser-based AI services through OpenAI-compatible APIs.

WebAI-to-API combines browser-native automation with WebAPI-based provider integrations to expose AI services through a flexible OpenAI-compatible API layer.

---

## Features

* OpenAI-compatible `/v1/chat/completions` API
* Provider-based architecture with unified routing
* Streaming response support (SSE)
* Conversation continuation support
* Health, readiness, and runtime diagnostics endpoints
* Docker deployment support
* Authentication management and browser login workflows

---

## Available Providers

### Gemini

Provides access to Google Gemini models through either the WebAPI backend or a browser-native Playwright runtime.

### Atlas Cloud

Provides access to cloud-hosted AI models through a native API integration powered by [Atlas Cloud](https://www.atlascloud.ai/?utm_source=github&utm_medium=link&utm_campaign=WebAI-to-API).

---

## Quick Start

> **Prerequisite:** Python `>=3.11,<3.13` (see `pyproject.toml`).
> **Windows:** Python `3.11.10+` or `3.12.4+` is required for secure Gemini WebAPI temporary cookie-cache handling. Linux and macOS require only the supported Python range above.

### 1. Host Setup

Run the setup wrapper from an existing Git checkout.

**Linux / macOS**
```bash
./install.sh
```

**Windows PowerShell**
```powershell
.\install.ps1
```

If PowerShell blocks the script, use this current-session-only fallback:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\install.ps1
```

This does not permanently change user or machine execution policy.

The wrapper validates Python and Poetry, runs bootstrap (including project
dependencies and Playwright Chromium) and diagnostics, and does not perform
login or start the server.

If Poetry was just installed but `poetry` is not found, reopen PowerShell. If it
is still unavailable, verify Poetry's user Scripts or install directory is on
`PATH`; `%APPDATA%\Python\Scripts` is a common location, but not universal.

Manual pre-Poetry fallback (without wrapper):
```bash
python scripts/bootstrap.py
```

Bootstrap creates `config.conf`, `.env` when its example exists, and the required
`runtime/` directory tree before installing dependencies and Chromium. Prefer
the wrappers; on Windows, `.\install.ps1` also selects a supported Python interpreter.

After bootstrap, run diagnostics:

```bash
poetry run python scripts/doctor.py
```

### 2. Configuration
The installer creates `config.conf` and `.env` from their example files when
missing. Review `config.conf` and edit settings as needed; do not copy example
files over existing configuration.

*For detailed settings (including logging verbosity and access log configurations), see [Configuration Guide](docs/configuration.md).*


### 3. Authenticate
```bash
poetry run python verify_login.py
```

### 4. Start the Server
```bash
poetry run python src/run.py
```

> [!TIP]
> New users can run `make setup` and `make doctor` for automated setup and diagnostics. See [Convenience Shortcuts](#optional-convenience-shortcuts) below.

---

## Updating

For host installations:

**Linux / macOS**

```bash
./update-linux-macos.sh
```

**Windows**

```cmd
update-windows.cmd
```

> [!NOTE] The updater checks `origin/master` and installs the update when the remote
`[project].version` differs from the locally installed version.

For Docker deployments:

```bash
git pull
APP_UID=$(id -u) APP_GID=$(id -g) docker compose up -d --build
```

Docker listens on container port `6969`. By default, Compose publishes it on
`127.0.0.1:6969` only. `WEB_PORT` changes only the host-side port; its default
is also `6969`.

Previous Docker behavior published on all host interfaces. Users requiring LAN
or remote access must opt in explicitly:

```env
DOCKER_BIND_ADDRESS=0.0.0.0
```

The project has no caller API authentication. Keep the default localhost bind,
or put external authentication in front of the entire service before exposing
it to an untrusted network.

Run `python scripts/bootstrap.py` first so `config.conf`, `.env`, and Docker's
runtime source exist. Docker defaults to `./runtime`; set `DOCKER_RUNTIME_DIR`
to choose a different host source mounted at `/app/runtime`. This is pre-Poetry host setup; Windows users should prefer
`.\install.ps1`. Non-Linux hosts can use the documented defaults or their Docker
Desktop equivalent; see the [Docker Deployment Guide](docs/docker.md).

See the [Updater Guide](docs/updating.md) for update checks, rollback behavior,
locking, protected files, and platform-specific details.

---

## Optional: Convenience Shortcuts

WebAI-to-API includes a bootstrap utility and a Makefile for common setup tasks.

| Command | Description |
|---------|-------------|
| `make setup` | One-step install, directory creation, and config setup. |
| `make doctor` | Run environment and dependency diagnostics. |

*Alternative (no Make): `python scripts/bootstrap.py`, then `poetry run python scripts/doctor.py`*

---

## Authentication

Gemini requires an authenticated Google session.

| Method | Recommended For |
|----------|----------|
| **Browser Login (`verify_login.py`)** | **Recommended.** Playwright backend, Docker, and long-term usage. |
| Manual Cookies | Quick testing and WebAPI-only usage. |

### 1. Browser Login (Recommended)
1. Run the interactive login helper:
   ```bash
   poetry run python verify_login.py
   ```
2. Complete the sign-in process in the browser window.
3. A successful login creates one shared configured auth-state file for Playwright and WebAPI, defaulting to `runtime/auth/gemini.json`.

For Docker, use the selected `DOCKER_RUNTIME_DIR` for both native login paths so
existing native auth overrides cannot redirect state outside the Docker mount:

```bash
RUNTIME_DIR=runtime AUTH_STATE_DIR=runtime/auth poetry run python verify_login.py
```

For `DOCKER_RUNTIME_DIR=/srv/webai/runtime`:

```bash
RUNTIME_DIR=/srv/webai/runtime AUTH_STATE_DIR=/srv/webai/runtime/auth poetry run python verify_login.py
```

### 2. Manual Cookies
1. Sign in to [Gemini](https://gemini.google.com/).
2. `__Secure-1PSID` is required; `__Secure-1PSIDTS` is optional. Copy available values from your browser cookies.
3. Paste available values into the `[Gemini]` section of `config.conf`.

---

## Send Your First Request

```bash
curl -X POST http://localhost:6969/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3-flash",
    "messages": [
      {
        "role": "user",
        "content": "Hello!"
      }
    ]
  }'
```

---

## Dashboard

Open the dashboard at `http://localhost:6969/ui`.

The dashboard provides a visual interface for runtime status, authentication management, API discovery, and interactive testing.

### Available Endpoints

| Endpoint | Purpose |
| --- | --- |
| `/v1/chat/completions` | OpenAI-compatible chat completions |
| `/v1/temporary/chat/completions` | Gemini WebAPI temporary chat |
| `/v1/models` | List supported models |
| `/v1/auth/status` | Check authentication status |
| `/v1/auth/login` | Start authentication flow |
| `/v1/conversations` | Manage Gemini WebAPI conversation snapshots |
| `/translate` | Legacy translation compatibility |
| `/ui/*` | Dashboard and playground |

### Temporary Gemini Chat

`/v1/temporary/chat/completions` is Gemini WebAPI-only and uses Gemini temporary requests (`temporary=True`).
Requests are not stored in Gemini history and do not create SQLite conversation snapshots.
It supports streaming, non-streaming, multimodal file inputs, and artifact outputs. See [docs/api.md](docs/api.md) for details.

### Legacy Translate Endpoint

The `/translate` endpoint is maintained for compatibility with the [Translate It!](https://github.com/iSegaro/Translate-It/) browser extension.

It uses stateless Gemini temporary requests with no conversation state. Independent requests can execute concurrently at the application layer. See [docs/api.md](docs/api.md) for additional details.

### File Support

File input is supported through OpenAI-style `content` parts on `/v1/chat/completions` when routed to the Gemini WebAPI backend.
The currently verified file formats are documented in [docs/api.md](docs/api.md).

> [!NOTE]
> File parts are supported only by the Gemini WebAPI backend in the MVP. Gemini Playwright and Atlas reject file parts with a clear capability error.
> For Gemini WebAPI, text content parts are concatenated into one prompt and file parts are passed as attachments. Exact text/file interleaving order is not preserved.
> The built-in `/ui/playground` page uses the same contract for file attachments.

---

## Supported Models

Available models may vary depending on the configured provider and backend.

Use the `/v1/models` endpoint to retrieve the current list of supported models.

---

## Model Routing

WebAI-to-API uses model prefixes to route requests to specific backends.

| Model | Backend |
|---------|---------|
| `gemini-3-flash` | Gemini (default configured backend) |
| `playwright/gemini-3-flash` | Gemini Playwright |
| `atlas/<model-id>` | Atlas Cloud via OpenAI-compatible API, with 50 validated chat models exposed in `/v1/models` |

> [!TIP]
> Model prefixes force backend selection and override the default Gemini backend configured in `config.conf`. Use `playwright/...` model prefixes to force the Playwright backend explicitly.

Gemini WebAPI and Playwright support Extended Thinking through `provider_options.gemini.extended_thinking`; see [API Documentation](docs/api.md).

---

## Documentation

- [API Documentation](docs/api.md)
- [Configuration Guide](docs/configuration.md)
- [Architecture Guide](docs/architecture.md)
- [Docker Deployment Guide](docs/docker.md)
- [Dashboard Guide](docs/dashboard.md)
- [Updater Guide](docs/updating.md)

Interactive API documentation is available through Swagger UI when the server is running.

---

## Star History

[![Star History Chart](https://star-history.dera.page/svg?repos=Amm1rr/WebAI-to-API\&type=Date)](https://star-history.dera.page/#Amm1rr/WebAI-to-API&Date)

---

## License

WebAI-to-API is licensed under the Apache License 2.0. See [LICENSE](LICENSE) for the full text.


<br>

[![](https://visitcount.itsvg.in/api?id=amm1rr\&label=V\&color=0\&icon=2\&pretty=true)](https://github.com/Amm1rr/)
