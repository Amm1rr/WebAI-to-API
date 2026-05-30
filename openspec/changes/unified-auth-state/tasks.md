## 1. Unified Authentication State Loader Layer

- [ ] 1.1 Create `src/app/services/browser/auth_loader.py` and define the `GeminiAuthStateLoader` interface.
- [ ] 1.2 Implement structure validation in the loader to check for mandatory cookie schema and keys.
- [ ] 1.3 Implement the translation parser inside `GeminiAuthStateLoader` to convert the unified cookie schema into direct HTTP client CurlCffi cookies.
- [ ] 1.4 Implement the translation parser in the loader to output the `storageState` schema for Playwright.

## 2. Configuration Decoupling and Hardening

- [ ] 2.1 Refactor `src/app/config.py` to remove any runtime cookie write capability or fallback `save_config` invocations from within provider endpoints.
- [ ] 2.2 Mark the `[Cookies]` section inside the default config structures and `config.conf.example` as deprecated.
- [ ] 2.3 Verify that configuration loader logic treats `config.conf` strictly as a read-only settings database.

## 3. Priority Resolution and Fallback Migration Pathway

- [ ] 3.1 Implement priority loading rules in `AuthManager` to first resolve authentication from `runtime/auth/gemini.json`.
- [ ] 3.2 Implement fallback checking in `AuthManager` to resolve legacy cookies from `config.conf` if `gemini.json` is missing or invalid.
- [ ] 3.3 Add structured deprecation warnings logged on fallback paths.
- [ ] 3.4 Implement exposing migration_needed metrics inside get_status() when Priority 2 fallback is active.

## 4. Providers Integration and Refactoring

- [ ] 4.1 Update `src/app/services/gemini_client.py` to initialize direct client sessions by bootstrapping cookies from `GeminiAuthStateLoader`.
- [ ] 4.2 Update `src/app/services/providers/gemini_playwright.py` to instantiate and lease contexts leveraging the shared loader translations.
- [ ] 4.3 Refactor the active login bootstrap monitoring inside `AuthManager` to save authentications exclusively via the loader.

## 5. Verification and Tests

- [ ] 5.1 Create unit tests for `GeminiAuthStateLoader` validating correct schema parsing and translation formatting.
- [ ] 5.2 Create unit tests verifying fallback cookie resolution, warning output, and migration-needed status exposure.
- [ ] 5.3 Verify that all 103 baseline tests continue to pass and execute with zero config-file persistence activity.
