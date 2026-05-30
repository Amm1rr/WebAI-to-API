# docker-compatible-auth Specification

## Purpose
TBD - created by archiving change docker-compatible-auth. Update Purpose after archive.
## Requirements
### Requirement: Configurable Direct Filesystem State Storage (`auth_state_dir`)
The system SHALL support a configurable directory path named `auth_state_dir` for loading and saving the pre-authenticated persistent state files, which represent serialized browser contexts rather than persistent browser profiles. The system SHALL load this path from the configuration file or environment variables, falling back to a default folder inside the working directory.

#### Scenario: Successful state loading from configurable directory
- **WHEN** a provider session initialization is executed
- **THEN** it SHALL resolve the state file path inside the configured `auth_state_dir` directory utilizing a deterministic provider-specific state file naming convention (e.g., `gemini.json`, `openai.json`, `claude.json`)
- **AND** it SHALL load the retrieved file into the Playwright `BrowserContext` on creation if the file exists

#### Scenario: Fallback on missing state file
- **WHEN** the resolved state file does not exist in the configured `auth_state_dir`
- **THEN** the session initialization SHALL continue with a clean, unauthenticated `BrowserContext`
- **AND** it SHALL NOT raise an error or crash the runtime engine

---

### Requirement: Session State Persistence Boundaries
The system SHALL enforce the guarantee that no automatic persistent Playwright storage state rewrites occur during normal request execution. The runtime API service is permitted to mutate in-memory Playwright browser contexts during request execution, but it SHALL NOT automatically overwrite or mutate the persistent state files on disk during active API requests (such as `/v1/chat/completions`). The on-demand login service and the manual bootstrap utility SHALL act as the exclusive writers of the persistent state files. Any runtime-triggered state saving SHALL be executed only inside explicit, isolated login workflows or manual bootstrap, and normal request execution paths MUST NOT execute background or active persistence tasks.

#### Scenario: In-memory context mutation without persistent rewrite
- **WHEN** a client completions request is processed and performs in-memory modifications (such as page creation or session cookie adjustments in browser memory)
- **THEN** the runtime API service SHALL NOT execute any file-level write, save, or overwrite operations on the persistent state files in `auth_state_dir`
- **AND** the filesystem-backed state file SHALL remain unmodified upon request completion or cancellation

#### Scenario: Shared Session Infrastructure Layer Ownership
- **WHEN** a state file is loaded or saved
- **THEN** it SHALL be executed exclusively by the shared session infrastructure layer
- **AND** the provider adapters SHALL NOT directly perform state loading, write operations, or file mutations

#### Scenario: Safe atomic state serialization
- **WHEN** the manual bootstrap utility, on-demand login service, or shared session infrastructure layer writes the session state to disk
- **THEN** it SHALL write the JSON payload to `{provider}.json.tmp` inside the `auth_state_dir`
- **AND** it SHALL execute a physical `fsync` on the file descriptor
- **AND** it SHALL atomically replace the target `{provider}.json` file using an atomic rename operation

### Requirement: Decoupled Manual Bootstrap Utility
The system SHALL provide a reusable manual bootstrap utility initially targeting Gemini. The utility SHALL launch a local browser in headful mode, guide the user through manual sign-in on the provider's web application, extract the resulting pre-authenticated persistent Playwright storage state payload, and write it atomically as a JSON state file inside the configured `auth_state_dir`.

#### Scenario: Successful manual bootstrapping
- **WHEN** the manual bootstrap utility is executed and the user completes the Google Gemini login interface
- **THEN** the manual bootstrap utility SHALL extract the pre-authenticated persistent Playwright storage state dictionary
- **AND** it SHALL write it atomically as `gemini.json` inside the configured `auth_state_dir` directory before closing the browser process

---

### Requirement: Headless Container Compatibility and Hardening
The browser engine SHALL launch the Chromium process in headless mode when the configured `headless` option is `True`. The containerized deployment environment SHALL utilize a base image containing all necessary system shared libraries required by Playwright to ensure error-free headless execution.

#### Scenario: Headless launch inside container
- **WHEN** the browser engine initializes Chromium in an environment where `headless` is set to `True`
- **THEN** it SHALL launch the Chromium process headlessly with sandbox bypass flags
- **AND** the browser process SHALL successfully establish connection and return the active generation ID

---

### Requirement: Graceful Container Lifecycle Teardown
During application shutdown, the FastAPI server lifespan cleanup SHALL close the browser engine and all active providers gracefully when the server receives a standard termination signal (`SIGTERM` or `SIGINT`).

#### Scenario: Clean teardown on container stop
- **WHEN** the FastAPI server receives a `SIGTERM` shutdown signal
- **THEN** the lifespan manager SHALL call cleanup operations on the shared session infrastructure layer to drain active requests, stop background loops, and close browser processes gracefully
- **AND** the Python process SHALL exit cleanly without leaving orphaned Chromium zombie processes

