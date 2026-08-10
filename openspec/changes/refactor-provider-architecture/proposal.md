## Why

The current architecture treats execution backends (Playwright vs. WebAPI) as top-level provider identities, leading to a "backend-centric" structure. This causes significant code duplication for Gemini-specific logic (tool parsing, prompt formatting, etc.) and leaks internal execution strategies into the public API. As the project prepares to support additional providers like ChatGPT or Claude, this model will become a major maintenance bottleneck.

## What Changes

- **Provider Consolidation**: Merging all Gemini implementations into a single logical `GeminiProvider` identity.
- **Backend Abstraction**: Introducing a "Backend Adapter" pattern where `GeminiProvider` internally selects its execution strategy (WebAPI or Playwright) via configuration. **GeminiProvider is the sole owner of this selection.**
- **ProviderFactory Refactor**: Restructuring the factory to resolve only logical provider identities (e.g., `gemini`, `atlas`). `ProviderFactory` SHALL NOT participate in backend or adapter selection.
- **Shared Runtime Boundary**: Formally establishing Playwright as a shared browser runtime component rather than a Gemini-specific backend. This does NOT involve redesigning core runtime components like `BrowserEngine` or `ProviderSession`.
- **Unified Configuration**: Centralizing backend selection in the project configuration (e.g., `[Gemini] backend = playwright`).
- **Compatibility Aliasing**: Maintaining temporary backward compatibility for the `playwright/` model prefix. `ProviderFactory` normalizes the prefix to the `gemini` provider identity, while `GeminiProvider` interprets the prefix to select the appropriate adapter.

## Capabilities

### New Capabilities
- `provider-adapter-framework`: A structured way for providers to handle multiple execution backends (Adapters) while sharing core provider logic.
- `backend-configuration-authority`: A centralized mechanism for selecting and validating provider execution strategies via project configuration.

### Modified Capabilities
- `modular-browser-engine`: Updating the engine's relationship with providers to ensure it remains a shared, backend-agnostic runtime.

## Impact

- **Affected Modules**: `src/app/services/factory.py`, `src/app/services/providers/`, `src/app/endpoints/`.
- **API**: `/v1/chat/completions` and `/v1/models` will have a cleaner, provider-oriented interface. Legacy endpoints MUST continue to function without behavioral regression.
- **Configuration**: New configuration keys for provider backends.
- **Dependencies**: Reduced coupling between endpoints and specific backend implementations.

**Note**: Any directory structures mentioned in related artifacts (e.g. `providers/gemini/`) are illustrative examples and not hard requirements for implementation.
