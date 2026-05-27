## ADDED Requirements

### Requirement: Request Abstraction
The system SHALL ensure that each provider is responsible for transforming the `OpenAIChatRequest` into its specific backend payload format.

#### Scenario: Atlas request transformation
- **WHEN** an `OpenAIChatRequest` is passed to the `AtlasProvider`
- **THEN** the provider SHALL map `messages`, `tools`, and `stream` to the Atlas Cloud API format.

### Requirement: Response Abstraction
The system SHALL ensure that each provider is responsible for normalizing its backend response into an OpenAI-compatible dictionary or object.

#### Scenario: Gemini response normalization
- **WHEN** Gemini returns a response with tool-call JSON in the text
- **THEN** the `GeminiProvider` SHALL parse the tool-call and format it according to the OpenAI `tool_calls` schema.
