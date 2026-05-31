## Context

The WebAI-to-API project is transitioning from a "Backend-centric" model, where each execution strategy (Playwright, WebAPI) is treated as a separate provider, to a "Provider-centric" model. Currently, `GeminiProvider` and `GeminiPlaywrightProvider` exist as independent entities in the `ProviderFactory`, causing duplication of Gemini-specific logic and leakage of internal strategies to the API client.

## Goals / Non-Goals

**Goals:**
- Consolidate Gemini implementations into a single `GeminiProvider` logical identity.
- Introduce a "Backend Adapter" pattern to encapsulate execution-specific logic (WebAPI vs. Playwright).
- Refactor `ProviderFactory` to route based on provider identity, not implementation.
- Establish Playwright as a shared browser runtime for all future browser-native providers.
- Implement configuration-driven backend selection for providers.
- **Ensure zero behavioral regression for legacy and specialized endpoints.**

**Non-Goals:**
- Redesigning the core `BrowserEngine`, `ProviderSession`, `ManagedPage`, or `PersistentTab` logic.
- Unifying WebAPI `SessionManager` with Playwright `ProviderSession`.
- Adding new providers (ChatGPT, Claude, etc.) in this change.
- Introducing a `backend` field in the public API request schema.

## Decisions

### 1. Provider Identity vs. Execution Strategy
**Decision:** Redefine "Provider" as a logical identity (e.g., Gemini) and "Adapter" as an execution strategy (e.g., Playwright).
**Rationale:** This allows the project to scale to multiple backends per provider without fragmenting provider-specific logic. It simplifies the API for clients and makes the system easier to maintain.
**Alternatives:** 
- Keep the current structure: Leads to code duplication and maintenance burden.
- Full unification of backends: Too complex and risky; WebAPI and Playwright have fundamentally different lifecycles.

### 2. Provider-Level Adapter Selection
**Decision:** Selection of the active backend will be handled **exclusively** by the `GeminiProvider` based on project configuration. `ProviderFactory` is restricted to resolving provider identities (gemini, atlas).
**Rationale:** Keeps the public API clean and ensures that the factory remains a high-level router while the provider owns its execution strategy.
**Alternatives:** 
- Request-level selection: Leaks internal strategy to the client and increases API complexity.

### 3. Shared Gemini Shared Logic
**Decision:** Extract shared Gemini logic (OpenAI formatting, tool parsing, prompt transformation) into shared utilities (e.g. `shared.py`) accessible to both adapters.
**Rationale:** Eliminates duplication and ensures consistent behavior across backends.
**Alternatives:** 
- Base classes: Composition is preferred over deep inheritance.

### 4. Backward Compatibility Aliasing
**Decision:** Preserve `playwright/` model prefix as a deprecated alias. `ProviderFactory` SHALL normalize this prefix to the `gemini` provider identity. `GeminiProvider` SHALL interpret the presence of the `playwright/` prefix to select and force the Playwright adapter.
**Rationale:** Avoids breaking existing client integrations while adhering to the ownership model where the provider owns strategy selection.
**Alternatives:** 
- Immediate removal: Breaking change.

## Risks / Trade-offs

- **[Risk] Implementation complexity in GeminiProvider** → [Mitigation] Use clear delegation to adapters and keep the main provider class as a coordinator.
- **[Risk] Configuration errors** → [Mitigation] Implement strict validation for the `backend` key and fail fast with descriptive errors.
- **[Risk] Regression in legacy endpoints** → [Mitigation] **MANDATORY**: Verify that `/gemini`, `/gemini-chat`, `/translate`, `/v1beta/models/{model}`, and `/v1/gems` continue to function identically.
