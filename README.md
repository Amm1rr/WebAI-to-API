# WebAI-to-API

<p align="center">
  <img src="./assets/Server-Run-WebAI.png" alt="WebAI-to-API Server" height="160" />
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

## Installation

### Prerequisites

```bash
poetry install
```

If you plan to use the Playwright backend:

```bash
poetry run playwright install chromium
```

### Configuration

```bash
cp config.conf.example config.conf
```

Edit `config.conf` to match your environment.

### Run

```bash
poetry run python src/run.py
```

---

## Quick Start

### Chat Completion

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

### Delete Gemini WebAPI Conversation

```bash
curl -X DELETE http://localhost:6969/v1/conversations/{conversation_id}
```

This endpoint deletes Gemini WebAPI conversations created through local SQLite-backed conversation snapshots. Playwright and Atlas conversations are not supported.

### Playwright Backend

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

## Supported Models

Available models may vary depending on the configured provider and backend.

Use the `/v1/models` endpoint to retrieve the current list of supported models.

---

## Documentation

- [API Documentation](docs/api.md)
- [Configuration Guide](docs/configuration.md)
- [Architecture Guide](docs/architecture.md)
- [Docker Deployment Guide](docs/docker.md)

Interactive API documentation is available through Swagger UI when the server is running.

---

## License

Starting from v0.5.0, WebAI-to-API is licensed under GNU AGPLv3.

Versions released before v0.5.0 remain available under the MIT License.

Commercial licensing options are available. See [COMMERCIAL_LICENSE.md](COMMERCIAL_LICENSE.md) for details.

---

## Contributing

By submitting contributions to this project, you agree to the terms described in [CLA.md](CLA.md).

---

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=Amm1rr/WebAI-to-API\&type=Date)](https://www.star-history.com/#Amm1rr/WebAI-to-API&Date)

<br>

[![](https://visitcount.itsvg.in/api?id=amm1rr\&label=V\&color=0\&icon=2\&pretty=true)](https://github.com/Amm1rr/)
