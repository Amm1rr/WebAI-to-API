## 1. Phase 0: Operational Safety (Highest Priority)

- [x] 1.1 Implement atomic file writing for `config.conf` to prevent corruption during concurrent updates.
- [x] 1.2 Refactor configuration and cookie persistence to use non-blocking I/O (e.g., `anyio.to_thread.run_sync`).
- [x] 1.3 Add a file-based lock or mutex to ensure sequential access to the configuration file during writes.

## 2. Phase 1: Gateway Stabilization and Decomposition

- [x] 2.1 Define the lightweight `BaseProvider` interface in `src/app/services/base.py`.
- [x] 2.2 Implement a static `ProviderFactory` in `src/app/services/factory.py` with hardcoded mappings.
- [x] 2.3 Create a shared SSE formatting utility that normalizes output ONLY at the client boundary.

## 3. Provider Refactoring (Provider-Owned Complexity)

- [x] 3.1 Refactor `AtlasProvider`: Localize `httpx` client management and native streaming logic.
- [x] 3.2 Refactor `GeminiProvider`: Localize cookie-based sessions, prompt injection, and simulated streaming logic.
- [x] 3.3 Move Gemini-specific tool-parsing and prompt-generation logic into the `GeminiProvider`.
- [x] 3.4 Ensure `GeminiProvider` correctly integrates with `SessionManager`.

## 4. Final Integration and Validation

- [x] 4.1 Update `chat.py` to delegate all request/response handling to the resolved provider.
- [x] 4.2 Verify that Phase 0 safety fixes prevent file corruption under high-concurrency rotation tests.
- [x] 4.3 Ensure no regressions in Gemini tool-calling or Atlas streaming behavior.
