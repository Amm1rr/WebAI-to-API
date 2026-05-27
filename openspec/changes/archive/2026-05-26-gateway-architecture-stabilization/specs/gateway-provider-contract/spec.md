## ADDED Requirements

### Requirement: Lightweight Provider Interface
The system SHALL define a minimal abstract interface for providers. This interface MUST NOT enforce internal implementation details like retries, session handling, or internal streaming mechanics.

#### Scenario: Orchestration via lightweight contract
- **WHEN** the gateway routes a request
- **THEN** it SHALL interact with the provider solely through a high-level `chat_completions` method that accepts an `OpenAIChatRequest` and returns a normalized response or stream.

### Requirement: Static Provider Registry
The system SHALL resolve providers via a simple, static mapping. Dynamic plugin discovery or loading is explicitly prohibited to maintain simplicity.

#### Scenario: Static resolution
- **WHEN** a provider is requested
- **THEN** the factory SHALL return an instance based on a hardcoded registry of supported providers.
