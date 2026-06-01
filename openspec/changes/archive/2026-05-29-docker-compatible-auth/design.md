## Context

The current Playwright browser runtime requires a manual, local, and headful initialization flow where the user must run a login utility that launches a visible Chromium browser on the host machine. In a Docker container or headless cloud deployment, this model fails because:
1. Graphical displays are absent, preventing headful browser execution and manual interaction.
2. Container filesystems are ephemeral or read-only, which can discard saved session states.
3. The lack of standard lifespan teardown in the server process risks leaving orphaned browser processes running inside the container when it is terminated.

To enable self-hosted production deployments, the project requires a Docker-compatible Playwright authentication flow. This must isolate the manual bootstrapping phase (saving a pre-authenticated persistent Playwright storage state locally) from the runtime API service (loading that state and running fully headless inside Docker).

## Goals / Non-Goals

**Goals:**
- **Decoupled Bootstrapping Workflow**: Separate the headful manual bootstrap utility (run once locally on a host with a UI to perform login) from the runtime API service (run continuously in the container). The manual bootstrap utility is a reusable flow initially targeting Gemini.
- **Configurable auth_state_dir Path**: Expose a configuration option named `auth_state_dir` to customize the filesystem directory containing the pre-authenticated persistent state files.
- **Deterministic File Naming**: Enforce deterministic provider-specific state file naming conventions (e.g. `gemini.json`, `openai.json`, `claude.json`) inside the `auth_state_dir`.
- **No Automatic State Rewrites**: Ensure no automatic persistent Playwright storage state rewrites occur during normal request execution. The runtime API service may mutate in-memory Playwright browser contexts during request execution, but it will not write or overwrite the persistent state files on disk during active API requests.
- **Clear Ownership of Auth State Persistence**: Define the manual bootstrap utility as the primary writer of persistent state files. The runtime API service primarily loads these state files.
- **Shared Session Infrastructure Layer Persistence**: Place all state loading and saving logic inside the shared session infrastructure layer. Remove any provider-specific persistence logic or state file mutations from provider adapters.
- **Atomic State Writes**: Enforce that state serialization uses safe, atomic write semantics (writing to a temporary file, executing `fsync`, and performing an atomic rename operation) to prevent JSON file corruption.
- **Headless Container Execution**: Configure the browser engine to run in headless mode when configured, optimizing browser launch parameters for containerized, sandboxed Chromium instances.
- **Graceful Lifespan Teardown**: Implement explicit lifespan cleanup hooks in FastAPI. During application shutdown, the lifespan cleanup closes the browser engine and registered providers gracefully, preventing zombie browser processes on container stop.

**Non-Goals:**
- **Runtime Refresh or Autosave in Phase 1**: Any automatic runtime state refresh, automatic session state updates, or background persistence tasks during request execution is explicitly out of scope for Phase 1.
- **Automated MFA or Login Solvers**: Automating login, solving CAPTCHAs, or bypassing authentication challenges dynamically in the runtime API service is out of scope.
- **Premature Multi-User Pools**: Implementing multi-user account pools or tenant isolation in this phase is out of scope.
- **Redesigning Provider Adapters**: Modifying the vendor-specific DOM selection or interaction sequences inside adapters is out of scope.

## Decisions

### 1. Configurable Direct Filesystem Persistence (`auth_state_dir`)
- **Choice**: The system will serialize the persistent Playwright storage state, not Chromium persistent browser profiles. We will support a configurable directory path named `auth_state_dir` loaded from configuration or environment variables. The runtime API service will directly read its pre-authenticated state file from this folder.
- **Deterministic File Naming**: State files will use a strict, deterministic provider-specific naming convention (e.g. `gemini.json`, `openai.json`, `claude.json`).
- **Ownership and No-Rewrite Semantics**: 
  - The manual bootstrap utility is the primary writer of the persistent Playwright storage state. The runtime API service primarily loads these state files during session setup.
  - The runtime API service may mutate in-memory Playwright browser contexts during request execution (e.g. adding tabs or modifying cookies in memory), but it will not write or overwrite the persistent state file on disk.
  - The runtime API service does not execute background persistence tasks.
  - State file saving operations are restricted to the shared session infrastructure layer and are invoked only by the manual bootstrap utility.
- **Shared Session Infrastructure Layer Domain**: All persistence, loading, and saving logic belongs to the shared session infrastructure layer. No provider adapters may directly perform state loading, write operations, or file mutations.
- **Atomic Write Semantics**: When writing state files (in the manual bootstrap utility), the shared session infrastructure layer MUST use atomic filesystem operations:
  1. Write the JSON payload to `{provider}.json.tmp` inside the `auth_state_dir`.
  2. Flush the file buffer and execute `fsync` to guarantee write persistence.
  3. Perform an atomic rename operation using `os.replace` to replace the target `{provider}.json` file.
  This prevents truncated or corrupted JSON state files during sudden process crashes or container termination.
- **Alternatives Considered & Future Roadmap**:
  - *Introducing IStateStore in Phase 1*: Rejected because it increases refactoring surface and adds premature complexity.
  - *Future Scaling Plan (Phase 2/3)*: As the service scales to multi-user or distributed remote-browser systems, an `IStateStore` boundary (abstracting `load_state` and `save_state` to support Redis or S3) will be introduced. Moving the filesystem logic to a clean configurable boundary in this phase ensures that this future migration remains trivial.

### 2. Decoupled Bootstrap Workflow (Initially Targeting Gemini)
- **Choice**: Refactor the verification script into a reusable manual bootstrap utility that:
  1. Launches the browser locally in headful (visible) mode.
  2. Navigates to Google Gemini.
  3. Waits for the user to complete the login process manually.
  4. Automatically extracts the pre-authenticated persistent Playwright storage state and saves it atomically as `gemini.json` in the configured `auth_state_dir`.
- **Rationale**: Keeps the production API container 100% headless and non-interactive. The user only runs the bootstrap utility once to set up the authentication state, which the headless container then mounts and reads.
- **Alternatives Considered**: Bundling VNC or a virtual desktop inside the runtime container. This was rejected due to high container weight, security risks, and high implementation complexity.

### 3. Official Playwright Base Image & Headless Containerization
- **Choice**: Utilize the official Playwright base image `mcr.microsoft.com/playwright/python:v1.40.0-jammy` for the `Dockerfile`. Update the browser launch parameters to support headless execution when running in a production container, utilizing sandboxing bypass flags (`--no-sandbox`, `--disable-dev-shm-usage`, etc.).
- **Rationale**: The official Playwright base image ensures that all required system shared libraries (`libnss3`, `libgbm1`, `libasound2`, etc.) are pre-installed and matched to the Playwright library version, preventing launcher crashes.
- **Alternatives Considered**: Using a slim Python base image and installing Chromium dependencies manually. This is fragile and highly prone to dependency mismatches.

### 4. Lifespan Cleanup Hooks
- **Choice**: Add explicit teardown hooks in the FastAPI lifespan context.
- **Rationale**: During application shutdown, the lifespan cleanup closes the browser engine and providers gracefully. When a container is stopped, it sends a `SIGTERM` signal. The lifespan cleanup ensures that active requests are drained, background loops are stopped, and browser processes are closed gracefully, preventing zombie browser processes on the host.

## Risks / Trade-offs

- **[Risk] Pre-authenticated Session Expiry**: Pre-authenticated session states can expire over time, requiring the user to periodically re-run the bootstrap tool.
  - *Mitigation*: The runtime API service will return a descriptive `401 Unauthorized` response with a clear message indicating authentication loss, and the manual bootstrap utility will provide a simple command to execute locally on the host machine to refresh the state file in the shared volume.
- **[Risk] State Write Serialization**: Concurrent writes to the same state file could lead to corruption.
  - *Mitigation*: Storage operations will be serialized using locks per provider session to avoid concurrent write clashes.
