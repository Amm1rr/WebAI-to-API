## Why

Authentication and session state for the Google Gemini integrations in WebAI-to-API are currently duplicated across two pathways: `gemini-webapi` stores cookies in `config.conf`, while Playwright-driven sessions utilize `runtime/auth/gemini.json`. This duplication creates significant operational complexity, leads to parallel authentication state lifecycle management, and causes configuration file pollution where `config.conf` is dynamically mutated at runtime for cookie storage. Decoupling authentication state from configuration and establishing a single canonical source of truth at `runtime/auth/` will simplify maintenance, clarify system ownership, and align with production-grade engineering principles.

## What Changes

- **Canonical State Store**: Migrate the canonical source of truth for all Gemini authentication and session data exclusively to `runtime/auth/` (specifically `gemini.json`).
- **Dedicated Auth State Abstraction**: Introduce a dedicated, extensible authentication abstraction layer (`GeminiAuthStateLoader`) to parse, validate, and translate authentication state into provider-specific formats.
- **Unified Adapter Integration**: Integrate `gemini-webapi` to bootstrap and load its authentication cookies dynamically from the shared `runtime/auth/gemini.json` file.
- **Strict Read-Only Configuration**: Restrict `config.conf` strictly to immutable application settings. Dynamically saving refreshed cookies or performing runtime cookie serialization into the config file is completely eliminated.
- **Robust Migration Pathway**: Provide a backward-compatible, prioritized cookie loading strategy. If `[Cookies]` exist in `config.conf`, they will continue to be supported as a deprecated read-only fallback. The system will log a deprecation warning and expose migration-needed metrics in `/v1/auth/status`, but will strictly avoid performing automatic runtime file writes or background migrations to `gemini.json` during normal request initialization. Migration to the canonical store must occur solely through explicit actions (such as on-demand login bootstrap workflows or explicit CLI migration utilities).

## Capabilities

### New Capabilities
- `unified-auth-state`: Defines the unified authentication state abstraction, loader layer, structure validation, and provider-specific translation contracts to support a single source of truth at `runtime/auth/`.

### Modified Capabilities
- `docker-compatible-auth`: Modifies state loading, loading priority, fallback policies, and restricts persistence boundaries to strictly prevent configuration file rewrites.

## Impact

- **Affected Code**: `src/app/config.py` (cookie resolution and default sections), `src/app/services/browser/auth_manager.py` (status validation and bootstrapping), `src/app/services/gemini_client.py` (client creation and session bootstrap), and endpoints that display auth status.
- **APIs**: GET `/v1/auth/status` will report canonical loading metrics, migration status, and warnings.
- **Dependencies**: Binds the direct HTTP client (`gemini-webapi`) and the browser automation provider (`playwright`) to a shared auth loading dependency.
