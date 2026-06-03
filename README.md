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

### Authentication

Gemini requires an authenticated Google session. Choose the authentication method that best matches your deployment.

#### Which Authentication Method Should I Use?

| Method | Recommended For |
|----------|----------|
| Manual Cookies | Quick testing and WebAPI-only usage |
| Browser Login (`verify_login.py`) | Playwright backend, Docker deployments, and long-term usage |

#### 1. Manual Cookies (Gemini WebAPI)
Fastest setup for lightweight use.
1. Sign in to [Gemini](https://gemini.google.com/).
2. Press **F12** to open Developer Tools.
3. Go to the **Network** tab and refresh the page.
4. Select any request to **gemini.google.com** and copy the values for `__Secure-1PSID` and `__Secure-1PSIDTS` from the **Cookies** or **Headers** tab.
5. Paste both values into the `[Gemini]` section of `config.conf`.

#### 2. Browser Login (Playwright)
Recommended for robustness and Docker deployments.
1. Run the interactive login helper:
   ```bash
   poetry run python verify_login.py
   ```
2. Complete the sign-in process in the browser window that opens.
3. This creates `runtime/auth/gemini.json`, which is automatically used by the Playwright backend and can also be used by the WebAPI backend when cookie configuration is not provided.

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

### Bulk Delete Gemini WebAPI Conversations

```bash
curl -X DELETE http://localhost:6969/v1/conversations
```

This endpoint best-effort deletes all locally persisted Gemini WebAPI conversation snapshots and their corresponding remote Gemini chats. Active conversations are skipped and reported in the response. Playwright and Atlas conversations are not supported.

### List Gemini WebAPI Conversations

```bash
curl http://localhost:6969/v1/conversations
```

This endpoint lists locally persisted Gemini WebAPI conversation snapshots from SQLite. It does not restore conversations or call Gemini remote APIs. Playwright and Atlas conversations are not included.

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
- [Dashboard Guide](docs/dashboard.md)

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
