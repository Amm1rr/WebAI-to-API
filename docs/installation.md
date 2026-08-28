# Installation Guide

This guide covers host installation, setup, diagnostics, and common Windows issues. For Docker deployment, see the [Docker Deployment Guide](docker.md).

## Prerequisites

* Python `>=3.11,<3.13`
* [Poetry](https://python-poetry.org/docs/#installation) available on `PATH`

On Windows, secure Gemini WebAPI temporary cookie-cache isolation requires Python `3.11.10+` or `3.12.4+`.

Install Poetry before running any setup command. `scripts/bootstrap.py` requires Poetry; it is not a pre-Poetry installer.

---

## Recommended Setup

Run a platform wrapper from an existing Git checkout.

### Linux / macOS

```bash
./install.sh
```

The wrapper selects a supported `python3` or `python` interpreter, verifies Poetry is available, then runs bootstrap and diagnostics.

### Windows PowerShell

```powershell
.\install.ps1
```

The wrapper selects a supported Python interpreter from `py -3.12`, `py -3.11`, or `python`, verifies Poetry is available, then runs bootstrap and diagnostics.

Both wrappers create setup state and validate the environment. They do not perform provider login or start the server.

---

## Windows Troubleshooting

If PowerShell blocks `install.ps1`, allow it for the current PowerShell session only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\install.ps1
```

This does not change user or machine execution policy permanently.

If Poetry was installed but `poetry` is not found, reopen PowerShell. If it is still unavailable, ensure Poetry's user Scripts or install directory is on `PATH`; `%APPDATA%\Python\Scripts` is a common location but not universal.

---

## Manual Setup

After Poetry is available, run:

```bash
python scripts/bootstrap.py
```

Bootstrap:

* validates Python and Poetry
* creates missing `config.conf` and `.env` from their examples without overwriting existing user configuration
* creates required runtime directories
* installs project dependencies and Playwright Chromium

Then run diagnostics:

```bash
poetry run python scripts/doctor.py
```

Doctor checks Python, configuration, Poetry, runtime directories, Playwright Chromium, authentication material, and local port availability. It does not start the server.

---

## Convenience Shortcuts

When GNU Make is available:

| Command | Description |
| --- | --- |
| `make setup` | Run `python scripts/bootstrap.py`. |
| `make doctor` | Run `python scripts/doctor.py`. |

---

## Next Steps

1. Review [Configuration Guide](configuration.md).
2. Set up provider authentication with `poetry run python verify_login.py` or another documented method in the Configuration Guide.
3. Start the server:

   ```bash
   poetry run python src/run.py
   ```

4. Use the [Docker Deployment Guide](docker.md) for container deployment and Docker-specific authentication.
