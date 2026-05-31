## Context

WebAI-to-API integrates with Google Gemini via two independent access pathways: the Playwright-driven browser session provider and the `gemini-webapi` direct HTTP wrapper. Historically, these two paths managed their authentication state separately. Playwright loaded and saved session context cookies and local storage state into `runtime/auth/gemini.json`. Conversely, `gemini-webapi` loaded cookies from the `[Cookies]` section of `config.conf` and periodically serialized updated cookies back into the application configuration file during runtime requests.

This creates several architectural problems:
1. **Duplicated Source of Truth**: Authentication data is stored in two completely separate formats and files.
2. **Configuration Pollution**: The `config.conf` file acts both as a static application config and as a mutable, dynamically rewritten runtime authentication database.
3. **Implicit State Drift**: Token rotation or cookie expiration in one pathway fails to automatically update or inform the other, leading to out-of-sync sessions.

## Goals / Non-Goals

**Goals:**
- **Single Source of Truth**: Establish `runtime/auth/gemini.json` as the exclusive canonical source of truth for all authentication and session state.
- **Dedicated Abstraction Layer**: Introduce a unified `GeminiAuthStateLoader` abstraction that loads, validates, and translates authentication state for both Playwright and direct HTTP client pathways.
- **Immutable Configuration**: Deprecate cookie storage and eliminate all runtime modifications, persistence writes, or cookie serialization into `config.conf`.
- **Prioritized Backward-Compatible Migration**: Ensure existing deployments utilizing legacy `[Cookies]` in `config.conf` continue to work seamlessly via read-only fallbacks, clear deprecation paths, and migration-needed reporting.
- **Extensible Concurrency Preservation**: Keep existing runtime contracts: `BrowserEngine` owns browser resources, and `AuthManager` coordinates bootstrap locking.

**Non-Goals:**
- Implementing named profiles, multi-account authentication pools, or automatic account switching (treated strictly as future extension points).
- Implementing Redis, Postgres, or distributed SaaS tenancy database-backed storage layers in the MVP.
- Scraping cookies from external Chrome profiles or implementing OAuth protocols.

## Decisions

### Decision 1: Dedicated State Loader Abstraction (`GeminiAuthStateLoader`)
We will introduce `GeminiAuthStateLoader` inside `src/app/services/browser/auth_loader.py` to act as the single data contract interface for authentication state.
- **Responsibilities**:
  - Load the canonical JSON payload from `runtime/auth/gemini.json`.
  - Validate JSON schema structures (checks for `cookies` array, mandatory keys like `name` and `value`).
  - Translate the unified schema into specific client adapter formats:
    - **Playwright format**: `storageState` structure (array of cookies and local storage items).
    - **`gemini-webapi` format**: Converts the cookies array into flat dictionary/headers suitable for CurlCffi/HTTP Client session setup.
- **Rationale**: Isolating auth loading and serialization parsing into a dedicated component prevents duplicate parser logic across provider files, while remaining highly extensible for future custom backends or named profiles.

### Decision 2: Canonical Storage Migration & Configuration Hardening
All runtime auth state updates (such as on-demand bootstrap logins) will write strictly to `runtime/auth/gemini.json`.
- **Config file write elimination**: We completely remove the `save_config` or configuration write logic inside request adapters or cookie rotation routines. The `config.conf` file is treated strictly as an immutable configuration read at startup.
- **State writing boundaries**: Only `AuthManager`'s login bootstrap flow and `verify_login.py` (via the shared session infrastructure layer) are permitted to write authentication state, using atomic serializations (`fsync` and replace on `gemini.json`).

### Decision 3: Backward-Compatible Priority Loading & Migration Path
To ensure smooth upgrades for existing users, `GeminiAuthStateLoader` implements the following priority resolution rules when establishing a session:
1. **Priority 1 (Canonical File)**: Look for `runtime/auth/gemini.json`. If it exists and contains valid authentication cookies, load state from it.
2. **Priority 2 (Legacy Config Fallback)**: If `gemini.json` is missing or invalid, check for the `[Cookies]` section inside `config.conf`. If cookies exist (e.g. `__Secure-1PSID`), load and boot the sessions using them.
3. **Deprecation Warnings**: If the legacy fallback is used, the system logs a clear deprecation warning: `"AuthManager: Loading legacy cookies from config.conf. This behavior is deprecated and will be removed in a future release. Please migrate your authentication state to runtime/auth/gemini.json."`
4. **Explicit Migration Requirement**: To enforce the absolute rule that normal API request paths remain strictly read-only, loading legacy cookies does NOT trigger automatic background writes or runtime file serialization into `gemini.json`. Instead, the system exposes a `migration_needed: true` status metric under the `/v1/auth/status` endpoint. The migration of legacy cookies into the canonical `gemini.json` store must be triggered through explicit out-of-band administrative actions (such as on-demand login bootstrap triggers or explicit CLI migration commands).

### Alternatives Considered:
- *Alternative 1: Keep both independent but synchronize them via active filesystems.* Rejected because it keeps `config.conf` mutable and violates configuration immutable state boundaries.
- *Alternative 2: Remove legacy `[Cookies]` immediately.* Rejected to preserve seamless backward compatibility and prevent breaking existing production deployments.

## Risks / Trade-offs

- **[Risk] State corruption under concurrent boot paths**  
  *Mitigation*: Ensure `GeminiAuthStateLoader` performs file reads safely, and `AuthManager.coordination_lock` debounces active writes.
- **[Risk] Incomplete schema parse on malformed `gemini.json`**  
  *Mitigation*: Implement strict structure validation in `GeminiAuthStateLoader`. If schema validation fails, gracefully log the error, treat the state as `INVALID_STATE`, and fall back to legacy `config.conf` or guest mode.
- **[Risk] Docker volume mounting path changes**  
  *Mitigation*: Maintain the same default `auth_state_dir = runtime/auth` configuration option so that existing mounts continue to point to the correct folder.
