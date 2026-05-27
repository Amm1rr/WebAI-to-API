## ADDED Requirements

### Requirement: Stateless Atlas Integration
The `AtlasProvider` SHALL provide a stateless implementation of the provider interface that proxies requests directly to the Atlas Cloud API.

#### Scenario: Atlas chat completion
- **WHEN** a chat completion request is sent to `AtlasProvider`
- **THEN** it SHALL use `httpx` to send a POST request to the Atlas API and return the response object.

### Requirement: Atlas Model Listing
The `AtlasProvider` SHALL return a list of supported Atlas models in OpenAI format.

#### Scenario: Listing Atlas models
- **WHEN** `list_models` is called on `AtlasProvider`
- **THEN** it SHALL return a list containing at least the `atlas/MiniMaxAI/MiniMax-M2` model.
