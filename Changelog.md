### Changelog – WebAI to API

#### v0.x.x – Draft

##### Fixed

- Added missing `nodriver` and `platformdirs` dependencies to `pyproject.toml` for `g4f` server compatibility.
- Unified server runner functions to implement a consistent and graceful shutdown mechanism.

---

#### v0.4.0 – 2025-06-27

##### Added

- Displayed a user message explaining how to use the `gpt4free` server.

##### Fixed

- Resolved execution issue on Windows 11.
- Improved error handling with appropriate user-facing messages.

##### Changed

- Updated internal libraries and dependencies.

---

#### v0.3.0 – 2025-06-25

##### Added

- Improved server startup information display, including available services and API endpoints.
- Added a new method using the [gpt4free v0.5.5.5](https://github.com/xtekky/gpt4free) library, which also functions as a fallback.
- Introduced support for switching between models using keyboard shortcuts (keys `1` and `2`) in the terminal.
- WebAI-to-API now uses your browser and cookies **only for Gemini**, resulting in faster performance.
- `gpt4free` integration provides access to multiple providers (ChatGPT, Gemini, Claude, DeepSeek, etc.), ensuring continuous availability of various models.

##### Changed

- Updated internal libraries.
- Upgraded to [Gemini API v1.14.0](https://github.com/HanaokaYuzu/Gemini-API).

##### Fixed

- Ensured compatibility with Windows (tested on Windows 11).
