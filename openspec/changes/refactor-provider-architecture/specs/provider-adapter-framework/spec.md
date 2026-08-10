## ADDED Requirements

### Requirement: Unified Gemini Provider Identity
The system SHALL expose a single `GeminiProvider` class as the primary entry point for all Gemini-related operations, regardless of the underlying execution backend.

#### Scenario: Instantiating the unified provider
- **WHEN** the system initializes the Gemini provider
- **THEN** it returns an instance of the unified `GeminiProvider` class

### Requirement: Provider Ownership of Adapter Selection
The `GeminiProvider` SHALL be the sole authority for selecting its execution adapter (e.g., WebAPI or Playwright). The `ProviderFactory` MUST NOT participate in this selection.

#### Scenario: Internal adapter selection
- **WHEN** a request is routed to `GeminiProvider`
- **THEN** `GeminiProvider` SHALL internally determine which adapter to use based on configuration or model prefix aliases.

### Requirement: Configuration-Driven Backend Selection
The system SHALL determine the backend for a provider based on the project configuration files (e.g., `config.conf`).

#### Scenario: Reading the backend from configuration
- **WHEN** a provider is initialized
- **THEN** the system reads the `backend` key from the provider's configuration section and validates it

### Requirement: Provider Identity Routing in Factory
The `ProviderFactory` SHALL resolve requests to logical provider identities (e.g., `gemini`, `atlas`) instead of technical implementations (e.g., `playwright`). It MAY normalize compatibility prefixes to a provider identity but MUST NOT participate in adapter selection.

#### Scenario: Normalizing a compatibility alias
- **WHEN** a request for a `playwright/gemini-pro` model is received
- **THEN** the `ProviderFactory` SHALL resolve the provider identity to `gemini` and SHALL pass the original model name (including prefix) to the provider.
- **AND** `ProviderFactory` MUST NOT select or force an execution adapter.

### Requirement: Provider Interpretation of Aliases
The `GeminiProvider` SHALL be responsible for interpreting compatibility aliases and selecting the corresponding adapter.

#### Scenario: Internal strategy selection via alias
- **WHEN** `GeminiProvider` receives a model name with the `playwright/` prefix
- **THEN** it SHALL select the Playwright adapter for that request, regardless of default configuration.

### Requirement: Shared Browser Runtime Boundary
The Playwright runtime components (`BrowserEngine`, `ProviderSession`, etc.) SHALL remain shared across all browser-native providers and MUST NOT be redesigned as part of this refactor.

#### Scenario: Using shared runtime for multiple providers
- **WHEN** multiple providers require browser-native execution
- **THEN** they all interact with the same `BrowserEngine` and `ProviderSession` infrastructure as per existing contracts.

### Requirement: Legacy Endpoint Regression Protection
The refactor SHALL NOT alter the behavior or contract of legacy and specialized endpoints.

#### Scenario: Verifying legacy functionality
- **WHEN** the refactor is complete
- **THEN** endpoints `/gemini`, `/gemini-chat`, `/translate`, `/v1beta/models/{model}`, and `/v1/gems` MUST remain functional and behave identically to their pre-refactor state.
