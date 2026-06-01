## 1. Unified Authentication State Loader Layer

- [x] 1.1 Create `src/app/services/browser/auth_loader.py` and define the `GeminiAuthStateLoader` interface.
- [x] 1.2 Implement structure validation in the loader to check for mandatory cookie schema and keys.
- [x] 1.3 Implement the translation parser inside `GeminiAuthStateLoader` to convert the unified cookie schema into direct HTTP client CurlCffi cookies.
- [x] 1.4 Implement the translation parser in the loader to output the `storageState` schema for Playwright.

## 2. Configuration Decoupling and Hardening

- [x] 2.1 Refactor `src/app/config.py` to remove any runtime cookie write capability or fallback `save_config` invocations from within provider endpoints.
- [x] 2.2 Mark the `[Cookies]` section inside the default config structures and `config.conf.example` as deprecated.
- [x] 2.3 Verify that configuration loader logic treats `config.conf` strictly as a read-only settings database.

## 3. Priority Resolution and Fallback Migration Pathway

- [x] 3.1 Implement priority loading rules in `AuthManager` to first resolve authentication from `runtime/auth/gemini.json`.
- [x] 3.2 Implement fallback checking in `AuthManager` to resolve legacy cookies from `config.conf` if `gemini.json` is missing or invalid.
- [x] 3.3 Add structured deprecation warnings logged on fallback paths.
- [x] 3.4 Implement exposing migration_needed metrics inside get_status() when Priority 2 fallback is active.

## 4. Providers Integration and Refactoring

- [x] 4.1 Update `src/app/services/gemini_client.py` to initialize direct client sessions by bootstrapping cookies from `GeminiAuthStateLoader`.
- [x] 4.2 Update `src/app/services/providers/gemini_playwright.py` to instantiate and lease contexts leveraging the shared loader translations.
- [x] 4.3 Refactor the active login bootstrap monitoring inside `AuthManager` to save authentications exclusively via the loader.

## 5. Verification and Tests

- [x] 5.1 Create unit tests for `GeminiAuthStateLoader` validating correct schema parsing and translation formatting.
- [x] 5.2 Create unit tests verifying fallback cookie resolution, warning output, and migration-needed status exposure.
- [x] 5.3 Verify that all 103 baseline tests continue to pass and execute with zero config-file persistence activity.
