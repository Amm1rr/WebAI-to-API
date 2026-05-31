## MODIFIED Requirements

### Requirement: Minimal Provider Adapter Interface
The system SHALL define a minimal `BaseProviderAdapter` interface that abstracts only vendor-specific authentication checks, URL state parsers, and prompt DOM submission sequences.

#### Scenario: Non-behavioral adapter execution
- **WHEN** the system executes an authentication check, extracts a conversation ID, or submits a prompt
- **THEN** it SHALL delegate that operation directly to the registered `BaseProviderAdapter` instance.
- **AND** the adapter SHALL NOT modify stream pipelines, serialization locks, or process orchestration pathways.

#### Scenario: Integration with Provider-centric architecture
- **WHEN** a provider (e.g., Gemini) is configured to use the Playwright backend
- **THEN** it SHALL use a provider-specific implementation of `BaseProviderAdapter` to interact with the shared browser runtime.
