## ADDED Requirements

### Requirement: Consistent SSE Streaming
The system SHALL provide a unified streaming utility that formats backend responses into OpenAI-compatible Server-Sent Events (SSE).

#### Scenario: Native streaming conversion
- **WHEN** a provider returns a native byte stream
- **THEN** the streaming utility SHALL wrap it and yield `data: <json_chunk>\n\n` messages.

### Requirement: Simulated Streaming Support
The system SHALL support simulating a stream for providers that only provide non-streaming responses by yielding the full content in a single valid OpenAI chunk followed by the `[DONE]` signal.

#### Scenario: Non-streaming backend simulation
- **WHEN** a backend returns a full text response instead of a stream
- **THEN** the utility SHALL yield one data chunk containing the full text and then `data: [DONE]`.
