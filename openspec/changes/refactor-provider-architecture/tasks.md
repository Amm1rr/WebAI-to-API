## 1. Preparation and Shared Logic Extraction

- [ ] 1.1 Extract shared Gemini logic (model resolution, tool parsing, prompt construction, OpenAI formatting) into a shared utility space within the Gemini provider scope.
- [ ] 1.2 Implement backend configuration support in `src/app/config.py` with validation for `webapi` and `playwright`.

## 2. Implementation of Backend Adapters

- [ ] 2.1 Refactor current WebAPI-based Gemini logic into an internal adapter.
- [ ] 2.2 Refactor current Playwright-based Gemini logic into an internal adapter.
- [ ] 2.3 Establish a internal contract/interface between the unified provider and its adapters.

## 3. Unified Gemini Provider Implementation

- [ ] 3.1 Implement the unified `GeminiProvider`.
- [ ] 3.2 Implement internal adapter selection logic within `GeminiProvider` (owning the choice between WebAPI and Playwright).
- [ ] 3.3 Ensure the unified `GeminiProvider` correctly delegates standard operations to the active adapter.

## 4. ProviderFactory Refactor

- [ ] 4.1 Update `ProviderFactory` to route the `gemini` key to the new unified `GeminiProvider`.
- [ ] 4.2 Remove `playwright` as a top-level provider identity from `ProviderFactory`.
- [ ] 4.3 Update `ProviderFactory` to normalize the `playwright/` model prefix to the `gemini` provider identity while ensuring the original model name is passed to the provider for internal strategy resolution.

## 5. Cleanup and Verification

- [ ] 5.1 Remove or archive deprecated standalone provider files.
- [ ] 5.2 Update imports in endpoints if required for runtime correctness, ensuring NO behavioral changes.
- [ ] 5.3 **MANDATORY**: Run regression tests for `/gemini`, `/gemini-chat`, `/translate`, `/v1beta/models/{model}`, and `/v1/gems`.
- [ ] 5.4 Run existing `/v1/chat/completions` tests to verify functional parity.
- [ ] 5.5 Update architecture documentation in `docs/` to reflect the provider-centric model.
