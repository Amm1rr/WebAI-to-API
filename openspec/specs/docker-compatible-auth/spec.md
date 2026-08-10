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
The system SHALL enforce the guarantee that no automatic persistent storage state rewrites occur during normal request execution. The runtime API service is permitted to mutate in-memory browser contexts and client states during request execution, but it SHALL NOT automatically overwrite or mutate the persistent state files on disk, nor SHALL it ever write to `config.conf` to store authentication cookies during active API requests. The on-demand login bootstrap service and the manual bootstrap utility SHALL act as the exclusive writers of the persistent state files. Any runtime-triggered state saving SHALL be executed only inside explicit, isolated login workflows or manual bootstrap, and normal request execution paths MUST NOT execute background or active persistence tasks.

#### Scenario: In-memory context mutation without persistent rewrite
- **WHEN** a client completions request is processed and performs in-memory modifications (such as page creation or session cookie adjustments in browser memory)
- **THEN** the runtime API service SHALL NOT execute any file-level write, save, or overwrite operations on the persistent state files in `auth_state_dir`
- **AND** the filesystem-backed state file SHALL remain unmodified upon request completion or cancellation

#### Scenario: Shared Session Infrastructure Layer Ownership
- **WHEN** a state file is loaded or saved
- **THEN** it SHALL be executed exclusively by the shared session infrastructure layer
- **AND** the provider adapters SHALL NOT directly perform state loading, write operations, or file mutations

#### Scenario: Safe atomic state serialization
- **WHEN** the manual bootstrap utility or shared session infrastructure layer writes the session state to disk
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

### Requirement: Priority Auth Loading and Legacy Fallback
The system SHALL support a prioritized authentication loading hierarchy that guarantees backward compatibility for legacy installations. When initializing a Gemini provider pathway, the system SHALL first attempt to load cookies from the canonical state file `runtime/auth/gemini.json`. If missing or invalid, the system SHALL fallback to load read-only from the legacy `[Cookies]` section of `config.conf` and log an official deprecation warning, while exposing a migration-needed status flag.

#### Scenario: Primary loading from canonical state file
- **WHEN** a Gemini session boots and a valid `runtime/auth/gemini.json` file is present
- **THEN** the system SHALL load the authentication state from this file
- **AND** it SHALL bypass any fallback check of legacy config sections

#### Scenario: Fallback to legacy configuration with migration warning
- **WHEN** a Gemini session boots and `runtime/auth/gemini.json` is missing but legacy `[Cookies]` are configured inside `config.conf`
- **THEN** the system SHALL load the legacy cookies read-only, boot the session successfully, log a clear deprecation warning
- **AND** it SHALL expose the `migration_needed: true` status flag in the auth status payload
- **AND** the system SHALL NOT write to the filesystem or trigger automatic background state file serialization

