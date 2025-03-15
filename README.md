## Disclaimer

**This is a research project. Please do not use it commercially and use it responsibly.**

<hr>

# WebAI-to-API

![Logo](assets/Server-Run.png)

WebAI-to-API is a modular web server built with FastAPI, designed to manage requests across AI services like Gemini and Claude. It supports configurable setups and streamlined integration. Please note:

- Currently, **Gemini** is functional.
- **Claude** is under development and will be supported soon.

---

## Features

- 🌐 **Endpoints Management**:
  - `/v1/chat/completions`
  - `/gemini`
  - `/claude`
- 🔄 **Service Switching**: Configure Gemini and Claude in `config.conf`.
- 🛠️ **Modular Architecture**: Easy to extend and maintain.

[![Endpoints Documentation](assets/Endpoints-Docs-Thumb.png)](assets/Endpoints-Docs.png)

---

## Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/Amm1rr/WebAI-to-API.git
   cd WebAI-to-API
   ```

2. Install dependencies using Poetry:

   ```bash
   poetry install
   ```

3. Create a configuration file:

   ```bash
   cp webaitoapi/config.conf.example webaitoapi/config.conf
   ```

4. Edit `webaitoapi/config.conf` to set up your desired service settings.

5. Run the server:
   ```bash
   poetry run python webaitoapi/main.py
   ```

---

## Usage

Send a POST request to `/v1/chat/completions`:

### Example Request

```json
{
  "model": "gemini",
  "messages": [{ "role": "user", "content": "Hello!" }]
}
```

### Example Response

```json
{
  "id": "chatcmpl-12345",
  "object": "chat.completion",
  "created": 1693417200,
  "model": "gemini",
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "Hi there!"
      },
      "finish_reason": "stop",
      "index": 0
    }
  ]
}
```

---

<details>

  <summary>

## Project Structure

  </summary>

```plaintext
.
├── assets
│   └── (Screenshots)
├── LICENSE
├── poetry.lock
├── Prompt.txt
├── pyproject.toml
├── README.md
├── requirements.txt
└── webaitoapi
    ├── config.conf.example
    ├── __init__.py
    ├── main.py
    └── models
        ├── claude.py
        └── gemini.py
```

</details>

---

## Roadmap

- ✅ Support for Gemini.
- 🟡 Development for Claude (stop development).

---

<details>
  <summary>
    <h2>Configuration ⚙️</h2>
  </summary>

### Key Configuration Options

| Section     | Option          | Description                   | Example Value |
| ----------- | --------------- | ----------------------------- | ------------- |
| [AI]        | default_ai      | /v1/chat/completions          | `gemini`      |
| [EnabledAI] | gemini, claude, | Enable/disable provider       | `true`        |
| [Browser]   | name            | Browser for cookie-based auth | `firefox`     |

The full configuration template is available in [`config.conf.example`](webaitoapi/config.conf.example).  
 Leave the cookies field empty to use `browser_cookies3` and the default browser selected in the config file for automatic authentication.

---

  <details>
    <summary>
      <h3>config.conf</h3>
    </summary>

    ```
    [AI]
    # Set the default AI service to be used.
    # Options: gemini, claude
    default_ai = gemini

    # Specify the default model for the Gemini AI service.
    # Available options:
    # "gemini-1.5-flash", "gemini-2.0-flash-exp", "gemini-1.5-pro"
    default_model_gemini = gemini-1.5-pro

    # Specify the default model for the Claude AI service.
    # Available options:
    # "claude-3-sonnet-20240229", "claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022"
    default_model_claude = claude-3-5-sonnet-20241022

    [Cookies]
    # Provide cookies required for the Claude AI service.
    claude_cookie =

    # Provide cookies required for the Gemini AI service.
    gemini_cookie_1psid =
    gemini_cookie_1psidts =

    [EnabledAI]
    # Enable or disable each AI service.
    # Use "true" to enable or "false" to disable.
    claude = false
    gemini = true

    [Browser]
    # Specify the default browser for any required operations.
    # Options: firefox, brave, chrome, edge, safari
    name = firefox
    ```

  </details>
</details>

- Located at `webaitoapi/config.conf`.
- Switch between Gemini and Claude services.
- Example configuration is provided in `config.conf.example`.

---

## License 📜

This project is open source under the [MIT License](LICENSE).

---

> **Note**: This is a research project. Please use it responsibly and avoid commercial use. Additional security configuration and error handling are required for production use.

<br>

[![](https://visitcount.itsvg.in/api?id=amm1rr&label=V&color=0&icon=2&pretty=true)](https://github.com/Amm1rr/)
