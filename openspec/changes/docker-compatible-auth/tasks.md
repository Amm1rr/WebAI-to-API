## 1. Setup and Configurations

- [ ] 1.1 Add `auth_state_dir` to the config schema in `config.conf`.
- [ ] 1.2 Implement the environment variable fallback for `auth_state_dir` in `src/app/config.py`.

## 2. Core Session & Browser Engine Refactoring

- [ ] 2.1 Update the shared session infrastructure layer to load pre-authenticated persistent state files from `auth_state_dir` utilizing deterministic file naming `{provider}.json`.
- [ ] 2.2 Disable background persistence tasks during active request execution.
- [ ] 2.3 Remove provider-specific persistence triggers from all provider adapters, delegating state loading entirely to the shared session infrastructure layer.
- [ ] 2.4 Refactor state persistence in the shared session infrastructure layer to perform atomic filesystem writes utilizing a temporary `{provider}.json.tmp` file, executing `fsync`, and performing an atomic rename operation.
- [ ] 2.5 Refactor the browser engine startup to launch Chromium headlessly with sandbox bypass parameters when headless configuration is enabled.

## 3. Decoupled Manual Bootstrap Utility

- [ ] 3.1 Refactor the manual sign-in validation script to act as a reusable manual bootstrap utility targeting Gemini that writes persistent Playwright storage state atomically as `gemini.json` inside the `auth_state_dir`.
- [ ] 3.2 Add terminal prompts to guide the user in local headful session verification and validation.

## 4. Container Deployment & Lifespan Hardening

- [ ] 4.1 Update the project `Dockerfile` to utilize the official Playwright Python base image.
- [ ] 4.2 Update `docker-compose.yml` to persist `auth_state_dir` via a volume mount.
- [ ] 4.3 Implement teardown hooks inside FastAPI's lifespan manager to close the browser engine and registered providers gracefully during application shutdown.
