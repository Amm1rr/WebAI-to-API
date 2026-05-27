## ADDED Requirements

### Requirement: Encapsulated Gemini Logic
The `GeminiProvider` SHALL encapsulate all logic related to Gemini web-API interaction, including cookie management, session initialization, and prompt-based tool-calling simulation.

#### Scenario: Tool-calling prompt injection
- **WHEN** a request with `tools` is sent to the `GeminiProvider`
- **THEN** the provider SHALL automatically inject the tools definition as a system prompt and parse the resulting tool-call from the model's text response.

### Requirement: Gemini Session Lifecycle
The `GeminiProvider` SHALL manage the lifecycle of its `MyGeminiClient` instance, ensuring it is properly initialized and closed.

#### Scenario: Session persistence
- **WHEN** multiple requests are sent to Gemini
- **THEN** the provider SHALL utilize the `SessionManager` to maintain conversation state if applicable.
