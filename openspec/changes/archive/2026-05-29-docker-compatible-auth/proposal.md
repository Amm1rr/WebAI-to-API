## Why

The current Playwright browser runtime requires a manual, local, and headful initialization flow where the user must run a login utility that launches a visible Chromium browser on the host machine. In a Docker container or headless cloud deployment, this model fails because:
1. Graphical displays are absent, preventing headful browser execution and manual interaction.
2. Container filesystems are ephemeral or read-only, which can discard saved session states.
3. The lack of standard lifespan teardown in the server process risks leaving orphaned browser processes running inside the container when it is terminated.

To enable self-hosted production deployments, the project requires a Docker-compatible Playwright authentication flow. This must isolate the manual bootstrapping phase (saving a pre-authenticated persistent Playwright storage state locally) from the runtime API service (loading that state and running fully headless inside Docker), utilizing a direct, filesystem-backed persistence model with a configurable storage directory.

## What Changes

- **Isolate Bootstrapping Workflow**: Separate the headful manual bootstrap utility (run once locally on a host with a UI to perform login) from the runtime API service (run continuously in the container). The manual bootstrap utility is a reusable workflow initially targeting Gemini.
- **Configurable auth_state_dir Path**: Expose a configuration option (in `config.conf` and environment variables) named `auth_state_dir` to customize the filesystem directory containing the pre-authenticated persistent state files.
- **No Automatic State Rewrites**: Ensure no automatic persistent Playwright storage state rewrites occur during normal request execution. The runtime API service may mutate in-memory Playwright browser contexts during request execution, but it will not overwrite the persistent state files on disk.
- **Decoupled Auth State Persistence**: Define the manual bootstrap utility as the primary writer of the persistent state files. The runtime API service primarily loads existing state files. Any automatic runtime refresh, background persistence tasks, or runtime file mutations are out of scope for Phase 1.
- **Shared Session Infrastructure Layer Ownership**: Consolidate all persistence, loading, and saving operations within the shared session infrastructure layer. Remove any provider-specific persistence logic or state file mutations from provider adapters.
- **Deterministic File Naming**: Define strict, deterministic provider-specific state file naming conventions within `auth_state_dir` (e.g. `gemini.json`, `openai.json`, `claude.json`).
- **Atomic State Writes**: Ensure manual bootstrap utility and shared session infrastructure layer writes use safe, atomic filesystem write semantics (writing to a temporary file, performing `fsync`, and performing an atomic rename operation) to prevent JSON file corruption during crashes or container termination.
- **Headless Container Execution**: Update the browser launch parameters to run in headless mode when configured, and optimize browser launch arguments for sandboxed container isolation.
- **Graceful Lifespan Teardown**: Integrate clean-up hooks in the FastAPI server lifespan. During application shutdown, the lifespan cleanup closes the browser engine and registered providers gracefully, preventing zombie browser processes on container stop.

## Capabilities

### New Capabilities
- `docker-compatible-auth`: Covers the decoupled manual bootstrap utility, configurable filesystem-backed `auth_state_dir` storage, shared session infrastructure layer loading, headless Chromium container execution, and graceful process teardown for the Playwright runtime.

### Modified Capabilities
<!-- No existing capabilities' requirements are modified; this is an incremental expansion of the Playwright runtime. -->

## Impact

- **Affected Components**:
  - Browser Engine: Dynamic browser launching, headless detection, and standard container arguments.
  - Session Management: Refactored state file path loading and saving utilizing a configurable `auth_state_dir` directory, disabling background persistence tasks during active runtime API service execution.
  - Provider Adapters: Removed all provider-specific persistence logic, delegating state file loading entirely to the shared session infrastructure layer.
  - API Lifecycle: Lifespan hooks to ensure proper cleanup of the browser engine and providers during application shutdown.
  - Bootstrapping: Refactored into a reusable manual bootstrap utility initially targeting Gemini that saves pre-authenticated state to `auth_state_dir`.
  - Deployment Assets: Upgraded Dockerfile to utilize a base image with all native Playwright libraries pre-packaged, along with volume mounts in Docker Compose to persist the bootstrapping state.
