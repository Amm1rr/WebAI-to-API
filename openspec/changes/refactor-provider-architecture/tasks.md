## 1. Preparation and Shared Logic Extraction

- [x] 1.1 Extract shared Gemini logic (model resolution, tool parsing, prompt construction, OpenAI formatting) into a shared utility space within the Gemini provider scope.
- [x] 1.2 Implement backend configuration support in `src/app/config.py` with validation for `webapi` and `playwright`.

## 2. Implementation of Backend Adapters

- [x] 2.1 Refactor current WebAPI-based Gemini logic into an internal adapter.
- [x] 2.2 Refactor current Playwright-based Gemini logic into an internal adapter.
- [x] 2.3 Establish a internal contract/interface between the unified provider and its adapters.

## 3. Unified Gemini Provider Implementation

- [x] 3.1 Implement the unified `GeminiProvider`.
- [x] 3.2 Implement internal adapter selection logic within `GeminiProvider` (owning the choice between WebAPI and Playwright).
- [x] 3.3 Ensure the unified `GeminiProvider` correctly delegates standard operations to the active adapter.

## 4. ProviderFactory Refactor

- [x] 4.1 Update `ProviderFactory` to route the `gemini` key to the new unified `GeminiProvider`.
- [x] 4.2 Remove `playwright` as a top-level provider identity from `ProviderFactory`.
- [x] 4.3 Update `ProviderFactory` to normalize the `playwright/` model prefix to the `gemini` provider identity while ensuring the original model name is passed to the provider for internal strategy resolution.

## 5. Cleanup and Verification

- [x] 5.1 Remove or archive deprecated standalone provider files.
- [x] 5.2 Update imports in endpoints if required for runtime correctness, ensuring NO behavioral changes.
- [x] 5.3 **MANDATORY**: Run regression tests for `/gemini`, `/gemini-chat`, `/translate`, `/v1beta/models/{model}`, and `/v1/gems`.
- [x] 5.4 Run existing `/v1/chat/completions` tests to verify functional parity.
- [x] 5.5 Update architecture documentation in `docs/` to reflect the provider-centric model.
