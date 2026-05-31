# unified-auth-state Specification

## Purpose
TBD - created by archiving change unified-auth-state. Update Purpose after archive.
## Requirements
### Requirement: Authentication State Loader Layer (`GeminiAuthStateLoader`)
The system SHALL provide a dedicated, extensible authentication state loader layer (`GeminiAuthStateLoader`). The loader SHALL be responsible for loading the canonical state JSON payload from `runtime/auth/gemini.json`, validating its structure, and translating it into provider-specific formats (e.g. CurlCffi cookie dictionaries for HTTP wrapper, or Playwright `storageState` objects).

#### Scenario: Successfully loading and validating valid canonical state file
- **WHEN** the authentication state loader parses a syntactically correct `gemini.json` state file
- **THEN** the system SHALL extract the cookies and return the translated provider-specific format
- **AND** the validation status SHALL be logged as successful

#### Scenario: Handling malformed state file structure
- **WHEN** the authentication state loader parses a corrupted or syntactically invalid `gemini.json` file
- **THEN** the loader SHALL catch the parsing exception, log an invalid state warning
- **AND** it SHALL propagate an invalid state error to trigger legacy fallbacks or guest mode

### Requirement: Future-Proof Profiles and Distributed Storage Extensibility
The `GeminiAuthStateLoader` interface SHALL be designed to be stateless and decoupled from specific storage backends. It SHALL support extensible profile parameter hooks to allow future implementations to resolve storage states from custom names, multi-account structures, or remote distributed storage services without modifying the core provider adapters.

#### Scenario: Extensibility check with custom storage profile parameter
- **WHEN** a custom named profile parameter is passed to the authentication loader layer
- **THEN** the loader interface SHALL expose hooks allowing custom profile resolution
- **AND** it SHALL preserve compatibility with the default process-bound file resolution

