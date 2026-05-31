## ADDED Requirements

### Requirement: Backend Configuration Validation
The system SHALL validate the `backend` configuration key for each provider upon startup or provider initialization.

#### Scenario: Invalid backend configuration
- **WHEN** a provider's `backend` is set to an unsupported value (e.g., `invalid-backend`)
- **THEN** the system SHALL fail fast with a clear configuration error message

### Requirement: Default Backend Selection
The system SHALL provide a sensible default for the `backend` configuration if the key is missing for a given provider.

#### Scenario: Missing backend configuration
- **WHEN** the `backend` key is missing for the Gemini provider
- **THEN** the system SHALL default to the legacy `webapi` backend to preserve existing behavior
