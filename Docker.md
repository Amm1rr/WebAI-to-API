# 🐳 Docker Deployment Guide

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/)
- [Docker Compose v2](https://docs.docker.com/compose/) (included with Docker Desktop)

---

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/leolionart/WebAI-to-API.git
cd WebAI-to-API
```

### 2. Create your config file

```bash
cp config.conf.example config.conf
```

Open `config.conf` and fill in your Gemini cookies:

```ini
[Cookies]
gemini_cookie_1psid     = YOUR___Secure-1PSID_HERE
gemini_cookie_1psidts   = YOUR___Secure-1PSIDTS_HERE
```

> **Where to get cookies:**
> 1. Log in to [gemini.google.com](https://gemini.google.com) in your browser
> 2. Open DevTools (`F12`) → **Application** → **Cookies** → `https://gemini.google.com`
> 3. Copy the values of `__Secure-1PSID` and `__Secure-1PSIDTS`

### 3. Start the server

```bash
docker compose up -d
```

The API is now available at **`http://localhost:6969`**.

---

## Cookie Persistence

Config is stored in a Docker named volume (`webai_data`) mapped to `/app/data/config.conf` inside the container. This means:

- Cookies survive container restarts and image updates
- The server automatically rotates `__Secure-1PSIDTS` every ~10 minutes and writes the updated value back to `config.conf` — so your session stays valid without manual intervention

---

## Useful Commands

| Command | Description |
|---------|-------------|
| `docker compose up -d` | Start in background |
| `docker compose down` | Stop and remove containers |
| `docker compose logs -f` | Stream live logs |
| `docker compose pull && docker compose up -d` | Update to latest image |
| `docker compose restart` | Restart without recreating |

Or use the provided `Makefile` shortcuts:

```bash
make up         # docker compose up -d
make down       # docker compose down
make logs       # docker compose logs -f
make pull       # docker compose pull
make restart    # down + up
```

---

## Building Locally

If you want to build the image from source instead of pulling from GHCR:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

Or edit `docker-compose.yml` directly: comment out the `image:` line and uncomment `build: .`.

---

## Changing the Port

Edit `docker-compose.yml` and update the port mapping:

```yaml
ports:
  - "8080:6969"   # expose on host port 8080 instead of 6969
```

---

## File Overview

```
.
├── Dockerfile              # Image build instructions
├── docker-compose.yml      # Main compose config (production)
├── docker-compose.dev.yml  # Local build override
├── config.conf             # Your config — cookies, model, proxy (gitignored)
└── config.conf.example     # Template to copy from
```
