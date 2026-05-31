# API Contract and Endpoint Classification

This document serves as the authoritative reference for maintainers and contributors regarding the project's API architecture, lifecycle guarantees, and persistence semantics.

## 1. Purpose

WebAI-to-API exposes multiple API surfaces to balance standard compatibility, legacy support, and specialized integration needs.

- **Primary APIs**: The core surface intended for all new integrations. Adheres to industry standards (OpenAI).
- **Compatibility APIs**: Bridges designed to emulate specific third-party protocols (e.g., Google Generative AI).
- **Legacy APIs**: Original endpoints maintained for backward compatibility with early versions of the project.
- **Specialized APIs**: Target-specific endpoints designed for a particular consumer (e.g., browser extensions).

## 2. Endpoint Classification Matrix

| Endpoint | Category | Recommended | Persistence | Streaming | Notes |
| :--- | :--- | :---: | :--- | :---: | :--- |
| `/v1/chat/completions` | Primary | Yes | SQLite-backed | Yes | Authoritative OpenAI-compatible surface. |
| `/v1/models` | Primary | Yes | N/A | No | Discovery endpoint for all providers. |
| `/v1/auth/status` | Primary | Yes | N/A | No | Real-time auth state and health diagnostics. |
| `/v1/auth/login` | Primary | Yes | N/A | No | Trigger for browser-based login workflows. |
| `/v1beta/models/{model}` | Compatibility | No | N/A | Yes | Google Generative AI compatibility bridge. |
| `/gemini` | Legacy | No | Stateless | Yes | Original MVP endpoint. No session state. |
| `/gemini-chat` | Legacy | No | In-memory | Yes | Simple session state; does not survive restarts. |
| `/translate` | Specialized | No | Shared In-memory | No | Shared global context for "Translate It!". |
| `/v1/gems` | Utilities | Yes | N/A | No | Gemini "Gems" enumeration. |

## 3. Primary Contract: /v1/chat/completions

The `/v1/chat/completions` endpoint is the authoritative API surface of the project. All maintainers must prioritize its stability and feature parity with the OpenAI Chat Completion spec.

- **Schema**: Strictly follows the OpenAI request/response format.
- **Streaming**: Supported via Server-Sent Events (SSE).
- **Provider Routing**: Requests are routed through the `ProviderFactory`.
- **Persistence**: Supported for providers implementing `PERSISTENT_RECOVERY` (e.g., `GeminiProvider`).
- **Isolation**: Every request is isolated by its `conversation_id`.

## 4. Conversation Contract

### `conversation_id`

- **Creation**: If not provided, a cryptographically secure 16-byte opaque token is generated.
- **Reuse**: Providing a valid `conversation_id` instructs the system to attempt session recovery.
- **Recovery**: Recovery depends on the provider. For `GeminiProvider`, this triggers a lookup in the SQLite repository.

### `reused_conversation`

A boolean field injected into the response metadata:
- `true`: The model response was generated within an existing, recovered session context.
- `false`: A new session was bootstrapped for this request.

## 5. Persistence Guarantees

Persistence semantics vary significantly across endpoints and must be clearly communicated to users.

| Endpoint | Restart Safe | Persistence Type | Recovery Mechanism |
| :--- | :---: | :--- | :--- |
| `/v1/chat/completions` | **Yes** | SQLite-backed | Serialized session restoration via repository. |
| `/gemini-chat` | No | In-memory only | Volatile; lost on server shutdown or crash. |
| `/translate` | No | Shared In-memory | Volatile; uses a singleton shared across all users. |
| `/gemini` | N/A | Stateless | Every request is a fresh, isolated session. |

## 6. Compatibility Layer Contract

### `/v1beta/models/{model}`

This endpoint is a **compatibility bridge**, not a full implementation of the Google Generative AI specification.

- **Goal**: Provide a Google Generative AI–style compatibility layer for integrations expecting Google-style request and response formats.
- **Non-Goal**: 100% protocol parity, full SDK compatibility, or complete metadata support.
- **Limitation**: Error codes and fine-grained metadata may not match official Google behavior.

## 7. Legacy Endpoint Policy

### `/gemini` and `/gemini-chat`

- **Status**: **Deprecated**.
- **Role**: Retained to avoid breaking early adopter scripts and simple integrations.
- **Maintenance**: Minimal. These endpoints should not receive new features (e.g., Tool Calling) unless they are trivial to pass through.
- **Migration**: All documentation and responses should guide users toward `/v1/chat/completions`.

## 8. Specialized Endpoint Policy

### `/translate`

- **Status**: **Supported (Specialized)**.
- **Context Sharing**: Intentionally uses a **shared global session** by design. This is optimized for high-frequency, short-prompt translation extension workloads.
- **Risk**: There is **no privacy isolation** between users of this endpoint as it uses a shared global session. It is intended primarily for personal or trusted environments.
- **Retention**: Maintained as long as the "Translate It!" extension remains a primary project use case.

## 9. Authentication Contract

Authentication is a decoupled lifecycle managed via `AuthManager` and specialized endpoints.

- **Status Monitoring**: `/v1/auth/status` provides a unified view of provider health.
- **Login Flow**: `/v1/auth/login` is a non-blocking trigger that initiates a browser-based workflow. It returns `202 Accepted` to indicate the process has started.
- **Recovery**: Authentication state is checked by providers at the start of each request. If auth is missing, providers must raise a `503 Service Unavailable` with a clear instruction to log in.

## 10. Provider Routing Contract

The architecture enforces a "Thin Gateway" pattern:

```text
/v1/chat/completions
        ↓
ProviderFactory (Resolves provider based on model prefix)
        ↓
Selected Provider (Handles request transformation & implementation)
```

- **Ownership**: The endpoint handler is responsible for routing and normalization.
- **Logic**: All implementation-heavy logic (session recovery, tool-call parsing, prompt construction) belongs to the **Provider**.
- **Transparency**: The gateway should remain agnostic of provider internals.

## 11. Future Evolution Policy

1. **Prioritize /v1**: All major architectural improvements must target the `/v1` namespace.
2. **Stability**: Legacy and Specialized endpoints must remain stable even if their underlying implementation is refactored.
3. **Contracts over Wrappers**: The structural API contracts defined here take precedence over any convenience wrappers or documentation summaries.
4. **Deprecation**: Removal of public endpoints should follow a documented deprecation process.
