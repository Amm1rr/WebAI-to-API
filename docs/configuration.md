# Configuration Guide

This document describes the available configuration and authentication methods supported by WebAI-to-API.

## Authentication Methods

WebAI-to-API supports multiple authentication approaches depending on the selected backend and deployment environment.

### Method A: Manual Cookies (Gemini WebAPI)

Configure Gemini authentication directly in `config.conf`:

```ini
[Gemini]
secure_1psid =
secure_1psidts =
```

#### Advantages

* Works in Docker and host environments
* No browser automation required
* Quick setup

#### Recommended For

* Gemini WebAPI deployments
* Headless environments
* Simple installations

---

### Method B: Browser Login (Playwright)

Generate browser authentication state:

```bash
poetry run python verify_login.py
```

This creates:

```text
runtime/auth/gemini.json
```

#### Advantages

* Native browser authentication
* Persistent login state
* Recommended Playwright workflow

#### Recommended For

* Playwright backend
* Docker + Playwright deployments
* Long-lived authenticated sessions

---

### Method C: Browser Cookie Discovery

WebAI-to-API can automatically retrieve cookies from supported browsers when explicit credentials are not provided.

Supported browsers depend on the local environment and available browser profiles.

---

## Authentication Comparison

| Method            | Backend    | Docker    | Persistence     |
| ----------------- | ---------- | --------- | --------------- |
| Manual Cookies    | WebAPI     | Yes       | Cookie lifetime |
| verify_login.py   | Playwright | Yes       | Persistent      |
| Browser Discovery | WebAPI     | Host Only | Cookie lifetime |
| /v1/auth/login    | Playwright | Host Only | Persistent      |

---

## Playwright Setup

Install Playwright browser binaries:

```bash
poetry run playwright install chromium
```

Generate authentication:

```bash
poetry run python verify_login.py
```

Verify status:

```bash
curl http://localhost:6969/v1/auth/status
```

---

## Basic Configuration

Example:

```ini
[Gemini]
backend = webapi
default_model = gemini-3-flash

[EnabledAI]
gemini = true

[Browser]
name = firefox

[Proxy]
http_proxy =
```

---

## Key Configuration Options

### Gemini

| Option          | Description                                  |
| --------------- | -------------------------------------------- |
| `backend`       | Execution backend (`webapi` or `playwright`) |
| `default_model` | Default Gemini model                         |

### Browser

| Option | Description                       |
| ------ | --------------------------------- |
| `name` | Browser used for cookie discovery |

### Proxy

| Option       | Description             |
| ------------ | ----------------------- |
| `http_proxy` | Optional outbound proxy |

### EnabledAI

| Option   | Description              |
| -------- | ------------------------ |
| `gemini` | Enable or disable Gemini |

---

## Docker Notes

For Playwright deployments:

1. Run authentication on the host machine.
2. Generate `runtime/auth/gemini.json`.
3. Start the Docker container.
4. Restart the container whenever authentication is refreshed.

See `Docker.md` for complete deployment instructions.

---

## Configuration Template

The full configuration template is available in:

```text
config.conf.example
```
