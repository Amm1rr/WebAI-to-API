# WebAI-to-API

<p align="center">
  <img src="./assets/Server-Run-WebAI.png" alt="WebAI-to-API Server" height="160" />
  <img src="./assets/Dashboard.png" alt="Dashboard" height="160" />
  <a href="https://www.atlascloud.ai/?utm_source=github&utm_medium=link&utm_campaign=WebAI-to-API">
    <img src="./assets/ATLAS_CLOUD_LOGO_BLACK.png" alt="Atlas Cloud" height="160" />
  </a>
</p>

**WebAI-to-API** is a browser-native AI runtime that exposes browser-based AI services through OpenAI-compatible APIs.

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

### 1. Install and Set Up

From an existing Git checkout, run the setup wrapper for your platform:

**Linux / macOS**
```bash
./install.sh
```

**Windows PowerShell**
```powershell
.\install.ps1
```

See the [Installation Guide](docs/installation.md) for prerequisites, manual setup, diagnostics, Windows troubleshooting, and Make shortcuts.

### 2. Configure

Review the generated `config.conf` and `.env` files, then configure the providers you want to use. See the [Configuration Guide](docs/configuration.md).

### 3. Authenticate

For browser-based Gemini authentication:

```bash
poetry run python verify_login.py
```

See the [Configuration Guide](docs/configuration.md) for authentication methods. For Docker authentication, see the [Docker Deployment Guide](docs/docker.md).

### 4. Start the Server

```bash
poetry run python src/run.py
```

---

## Updating

See the [Updater Guide](docs/updating.md) for host updates and the [Docker Deployment Guide](docs/docker.md) for Docker rebuilds and updates.

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

Open the dashboard at `http://localhost:6969/ui`. It provides local runtime inspection, authentication management, API discovery, and interactive testing. See the [Dashboard Guide](docs/dashboard.md).

---

## Supported Models

Available models depend on configured providers and runtime availability. Use `/v1/models` as the authoritative current catalog. Model prefixes can select a provider or backend; see [API Documentation](docs/api.md) for routing and endpoint behavior.

---

## Documentation

* [Installation Guide](docs/installation.md)
* [API Documentation](docs/api.md)
* [Configuration Guide](docs/configuration.md)
* [Architecture Guide](docs/architecture.md)
* [Docker Deployment Guide](docs/docker.md)
* [Dashboard Guide](docs/dashboard.md)
* [Updater Guide](docs/updating.md)

Interactive API documentation is available through Swagger UI when the server is running.

---

## Star History

[![Star History Chart](https://star-history.dera.page/svg?repos=Amm1rr/WebAI-to-API\&type=Date)](https://star-history.dera.page/#Amm1rr/WebAI-to-API&Date)

---

## License

WebAI-to-API is licensed under the Apache License 2.0. See [LICENSE](LICENSE) for the full text.


<br>

[![](https://visitcount.itsvg.in/api?id=amm1rr\&label=V\&color=0\&icon=2\&pretty=true)](https://github.com/Amm1rr/)
