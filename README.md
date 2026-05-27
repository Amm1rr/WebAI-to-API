## Disclaimer

> **This project is intended for research and educational purposes only.**  
> Please refrain from any commercial use and act responsibly when deploying or modifying this tool.

---

# WebAI-to-API

<p align="center">
  <img src="./assets/Server-Run-WebAI.png" alt="WebAI-to-API Server" height="160" />
  <img src="./assets/Server-Run-G4F.png" alt="gpt4free Server" height="160" />
  <a href="https://www.atlascloud.ai/?utm_source=github&utm_medium=link&utm_campaign=WebAI-to-API">
    <img src="./assets/ATLAS_CLOUD_LOGO_BLACK.png" alt="Atlas Cloud" height="160" />
  </a>
</p>

**WebAI-to-API** is a modular web server built with FastAPI that allows you to expose your preferred browser-based LLM (such as Gemini) as a local API endpoint.

---

This project supports **three operational modes**:

1. **Primary Web Server**

   > WebAI-to-API

   Connects to the Gemini web interface using your browser cookies and exposes it as an API endpoint. This method is lightweight, fast, and efficient for personal use.

2. **Atlas Cloud Provider**

   > [Atlas Cloud](https://www.atlascloud.ai/?utm_source=github&utm_medium=link&utm_campaign=WebAI-to-API)

   A direct API provider offering high-performance access to advanced models like MiniMax-M2. Ideal for developers seeking a reliable, low-latency cloud alternative with native OpenAI compatibility.

3. **Fallback Web Server (gpt4free)**

   > [gpt4free](https://github.com/xtekky/gpt4free)

   A secondary server powered by the `gpt4free` library, offering broader access to multiple LLMs beyond Gemini, including:

   - ChatGPT
   - Claude
   - DeepSeek
   - Copilot
   - HuggingFace Inference
   - Grok
   - ...and many more.

This design provides both **speed and redundancy**, ensuring flexibility depending on your use case and available resources.

---

## Features

- 🌐 **Available Endpoints**:

  - **WebAI Server**:
    - `/v1/chat/completions` (OpenAI-compatible)
    - `/v1/models` (List all providers and models)
    - `/gemini`
    - `/gemini-chat`
    - `/translate`
    - `/v1beta/models/{model}` (Google Generative AI v1beta API)

- 🛠️ **Refactored Architecture**: Decoupled gateway logic with a lightweight provider contract.
  - **Thin Gateway**: `chat.py` acts as a clean orchestrator.
  - **Provider-Owned Complexity**: Each backend (Gemini, Atlas) manages its own transformation and streaming quirks.
  - **Atomic Persistence**: Concurrency-safe cookie rotation and configuration updates.

<p align="center">
  <img src="./assets/Endpoints-Docs.png" alt="Endpoints" height="280" />
</p>

---

## Installation

1. **Clone the repository:**

   ```bash
   git clone https://github.com/Amm1rr/WebAI-to-API.git
   cd WebAI-to-API
   ```

2. **Install dependencies using Poetry:**

   ```bash
   poetry install
   ```

3. **Create and update the configuration file:**

   ```bash
   cp config.conf.example config.conf
   ```

   Then, edit `config.conf` to adjust service settings and other options.

4. **Run the server:**

   ```bash
   poetry run python src/run.py
   ```

---

## Usage

Send a POST request to `/v1/chat/completions` (or any other available endpoint) with the required payload.

### Supported Models

| Model                       | Description                        |
| --------------------------- | ---------------------------------- |
| `gemini-3-pro`              | Most powerful model                |
| `gemini-3-flash`            | Fast and efficient model (default) |
| `gemini-3-flash-thinking`   | Enhanced reasoning model           |

### Example Request (Basic)

```json
{
  "model": "gemini-3-pro",
  "messages": [{ "role": "user", "content": "Hello!" }]
}
```

### Example Request (With System Prompt & Conversation History)

```json
{
  "model": "gemini-3-flash-thinking",
  "messages": [
    { "role": "system", "content": "You are a helpful assistant." },
    { "role": "user", "content": "What is Python?" },
    { "role": "assistant", "content": "Python is a programming language." },
    { "role": "user", "content": "Is it easy to learn?" }
  ]
}
```

### Example Response

```json
{
  "id": "chatcmpl-12345",
  "object": "chat.completion",
  "created": 1693417200,
  "model": "gemini-3.0-pro",
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "Hi there!"
      },
      "finish_reason": "stop",
      "index": 0
    }
  ],
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  }
}
```

---

## Documentation

### WebAI-to-API Endpoints

> `POST /gemini`

Initiates a new conversation with the LLM. Each request creates a **fresh session**, making it suitable for stateless interactions.

> `POST /gemini-chat`

Continues a persistent conversation with the LLM without starting a new session. Ideal for use cases that require context retention between messages.

> `POST /translate`

Designed for quick integration with the [Translate It!](https://github.com/iSegaro/Translate-It) browser extension.
Functionally identical to `/gemini-chat`, meaning it **maintains session context** across requests.

> `POST /v1/chat/completions`

**OpenAI-compatible endpoint** with full support for:
- **System prompts**: Set behavior and context for the assistant
- **Conversation history**: Maintain context across multiple turns (user/assistant messages)
- **Streaming**: Optional streaming response support

Built for seamless integration with clients that expect the OpenAI API format.

> `POST /v1beta/models/{model}`

**Google Generative AI v1beta API** compatible endpoint.
Provides access to the latest Google Generative AI models with standard Google API format including safety ratings and structured responses.

---

### gpt4free Endpoints

<details>
  <summary>
    <b>Available Endpoints (gpt4free API Layer)</b>
  </summary>

These endpoints follow the **OpenAI-compatible structure** and are powered by the `gpt4free` library.  
For detailed usage and advanced customization, refer to the official documentation:

- 📄 [Provider Documentation](https://github.com/gpt4free/g4f.dev/blob/main/docs/selecting_a_provider.md)
- 📄 [Model Documentation](https://github.com/gpt4free/g4f.dev/blob/main/docs/providers-and-models.md)

```
GET  /                              # Health check
GET  /v1                            # Version info
GET  /v1/models                     # List all available models
GET  /api/{provider}/models         # List models from a specific provider
GET  /v1/models/{model_name}        # Get details of a specific model

POST /v1/chat/completions           # Chat with default configuration
POST /api/{provider}/chat/completions
POST /api/{provider}/{conversation_id}/chat/completions

POST /v1/responses                  # General response endpoint
POST /api/{provider}/responses

POST /api/{provider}/images/generations
POST /v1/images/generations
POST /v1/images/generate            # Generate images using selected provider

POST /v1/media/generate             # Media generation (audio/video/etc.)

GET  /v1/providers                  # List all providers
GET  /v1/providers/{provider}       # Get specific provider info

POST /api/{path_provider}/audio/transcriptions
POST /v1/audio/transcriptions       # Audio-to-text

POST /api/markitdown                # Markdown rendering

POST /api/{path_provider}/audio/speech
POST /v1/audio/speech               # Text-to-speech

POST /v1/upload_cookies             # Upload session cookies (browser-based auth)

GET  /v1/files/{bucket_id}          # Get uploaded file from bucket
POST /v1/files/{bucket_id}          # Upload file to bucket

GET  /v1/synthesize/{provider}      # Audio synthesis

POST /json/{filename}               # Submit structured JSON data

GET  /media/{filename}              # Retrieve media
GET  /images/{filename}             # Retrieve images
```

</details>

---

## Roadmap

- ✅ Maintenance

---

<details>
  <summary>
    <h2>Configuration ⚙️</h2>
  </summary>

### Key Configuration Options

| Section     | Option     | Description                                | Example Value           |
| ----------- | ---------- | ------------------------------------------ | ----------------------- |
| [AI]        | default_ai | Default service for `/v1/chat/completions` | `gemini`                |
| [Browser]   | name       | Browser for cookie-based authentication    | `chrome`               |
| [EnabledAI] | gemini     | Enable/disable Gemini service              | `true`                  |
| [Proxy]     | http_proxy | Proxy for Gemini connections (optional)    | `http://127.0.0.1:2334` |

The complete configuration template is available in [`WebAI-to-API/config.conf.example`](WebAI-to-API/config.conf.example).  
If the cookies are left empty, the application will automatically retrieve them using the default browser specified.

---

### Sample `config.conf`

```ini
[AI]
# Default AI service.
default_ai = gemini

# Default model for Gemini (options: gemini-3-pro, gemini-3-flash, gemini-3-flash-thinking)
default_model_gemini = gemini-3-flash

# Gemini cookies (leave empty to use browser_cookies3 for automatic authentication).
gemini_cookie_1psid =
gemini_cookie_1psidts =

[EnabledAI]
# Enable or disable AI services.
gemini = true

[Browser]
# Default browser options: firefox, brave, chrome, edge, safari.
name = firefox

# --- Proxy Configuration ---
# Optional proxy for connecting to Gemini servers.
# Useful for fixing 403 errors or restricted connections.
[Proxy]
http_proxy =
```

</details>

---

## Project Structure

The project now follows a modular layout that separates configuration, business logic, API endpoints, and utilities:

```plaintext
src/
├── app/
│   ├── main.py                # FastAPI app creation and lifespan management.
│   ├── config.py              # Global configuration loader.
│   ├── endpoints/             # API endpoint routers.
│   │   ├── chat.py            # Clean orchestrator for /v1/chat/completions.
│   │   └── ...
│   ├── services/              # Business logic and provider systems.
│   │   ├── base.py            # Lightweight provider interface contract.
│   │   ├── factory.py         # Static provider registry (lazy initialization).
│   │   ├── providers/         # Encapsulated backend implementations.
│   │   │   ├── gemini.py      # Browser-based session & prompt emulation.
│   │   │   └── atlas.py       # Stateless HTTP-native integration.
│   │   ├── gemini_client.py   # Gemini low-level client initialization.
│   │   └── ...
│   └── utils/
│       ├── config_utils.py    # Atomic, non-blocking config persistence.
│       ├── streaming.py       # Shared SSE normalization utility.
│       └── browser.py         # Browser-based cookie retrieval.
├── models/                    # Model wrappers.
└── schemas/                   # Pydantic validation schemas.
```

---

## Developer Documentation

### Overview

The project is built on a modular architecture designed for scalability and ease of maintenance. Its primary components are:

- **app/endpoints/chat.py**: Acts as a thin orchestrator that resolves the correct provider via the `ProviderFactory` and delegates the completion request.
- **app/services/factory.py**: A static registry that lazily initializes provider instances based on model prefixes or explicit provider flags.
- **app/services/providers/**: Encapsulates provider-specific logic. Each provider (e.g., `GeminiProvider`) is responsible for its own request mapping, response normalization, and streaming mechanics.
- **app/utils/config_utils.py**: Ensures operational safety by providing atomic, non-blocking configuration persistence for volatile state like rotated cookies.

### How It Works

1. **Application Initialization:**  
   On startup, the application loads configurations and initializes the Gemini client and session managers. This is managed via the `lifespan` context in `app/main.py`.

2. **Routing & Resolution:**  
   The `chat.py` endpoint uses the `ProviderFactory` to resolve the appropriate provider based on the request model or provider field.

3. **Delegated Implementation:**  
   Each provider implements a lightweight contract. The orchestrator remains clean, while the providers handle implementation-heavy work like prompt transformation, tool-call parsing, and internal streaming states.

4. **Normalization & Persistence:**  
   Responses are normalized to OpenAI format at the provider/SSE boundary. Any state changes (like cookie rotation) are persisted atomically to prevent configuration corruption.

---

## 🐳 Docker Deployment Guide

For Docker setup and deployment instructions, please refer to the [Docker.md](Docker.md) documentation.

---

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=Amm1rr/WebAI-to-API&type=Date)](https://www.star-history.com/#Amm1rr/WebAI-to-API&Date)

## License 📜

This project is open source under the [MIT License](LICENSE).

---

> **Note:** This is a research project. Please use it responsibly, and be aware that additional security measures and error handling are necessary for production deployments.

<br>

[![](https://visitcount.itsvg.in/api?id=amm1rr&label=V&color=0&icon=2&pretty=true)](https://github.com/Amm1rr/)
