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
| `/v1/chat/completions` | Primary | Yes | Provider/backend-dependent | Yes | Authoritative OpenAI-compatible surface. |
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
- **Persistence**: Provider/backend-dependent. The selected provider and adapter define whether `conversation_id` maps to local snapshots, provider-side conversation URLs, or no persisted state.
- **Isolation**: Every request is isolated by its `conversation_id`.

## 4. Conversation Contract

### `conversation_id`

- **Creation**: If not provided, a cryptographically secure 16-byte opaque token is generated.
- **Reuse**: Providing a valid `conversation_id` instructs the selected provider/backend to attempt continuation according to its own recovery mechanism.
- **Recovery**: Recovery depends on the provider/backend:
  - **Gemini WebAPI**: Uses SQLite-backed session snapshots through `SessionRegistry` and `SQLiteConversationRepository`.
  - **Gemini Playwright**: Uses Gemini provider-side conversation URLs (`https://gemini.google.com/app/{conversation_id}`) and reuses in-memory `PersistentTab` instances when available. It does not use SQLite conversation snapshots.
  - **Atlas**: Stateless pass-through provider. It does not consume or persist `conversation_id`.

### `reused_conversation`

A boolean field injected into the response metadata:
- **Gemini WebAPI**:
  - `true`: An existing or restored `ChatSession` was reused.
  - `false`: A new `ChatSession` was bootstrapped.
- **Gemini Playwright**:
  - `true`: An in-memory `PersistentTab` for the conversation was reused.
  - `false`: No in-memory tab was reused. The backend may still resume the provider-side Gemini thread by navigating to the conversation URL.
- **Stateless providers**: This field may be absent or provider-defined because no local conversation state is maintained.

## 5. Persistence Guarantees

Persistence semantics vary significantly across endpoints and across `/v1/chat/completions` providers/backends. They must be clearly communicated to users.

| Endpoint / Backend | Restart Safe | Persistence Type | Recovery Mechanism |
| :--- | :---: | :--- | :--- |
| `/v1/chat/completions` - Gemini WebAPI | Yes | SQLite-backed snapshots | Serialized `ChatSession` restoration via repository. |
| `/v1/chat/completions` - Gemini Playwright | Provider-dependent | Provider-side URL-backed | Navigate to `https://gemini.google.com/app/{conversation_id}`; reuse `PersistentTab` when still in memory. |
| `/v1/chat/completions` - Atlas | No | Stateless | No local conversation persistence; requests are forwarded independently. |
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

Authentication source handling is intentionally split by responsibility:

| Responsibility | Owner | Contract |
| :--- | :--- | :--- |
| Discovery | `AuthLoader` | Finds available auth material, but does not decide the winning source. |
| Selection | Provider-specific selector | Defines source priority and fallback sequencing. Gemini uses `GeminiAuthSelector`. |
| Validation | Backend implementation | Validates whether selected auth material is usable for that backend. |
| Activation | Backend implementation | Activates a WebAPI client or browser context. |
| Caching | `AuthManager` | Owns cached status returned by `/v1/auth/status`. |
| Login/recovery orchestration | `AuthManager` and provider auth strategy | Coordinates login, status refresh, and provider-specific post-login recovery. |

For Gemini, selector priority is `[Gemini]` canonical cookies, then legacy `[Cookies]` cookies, then `runtime/auth/gemini.json`.

WebAPI utilizes all sources, prioritizing config cookies for direct client initialization. Playwright **strictly requires** valid browser storage state from `runtime/auth/gemini.json` and will not fall back to raw config cookies.

WebAPI performs account-status validation and client activation after selection, including guest fallback decisions. Playwright performs browser storage-state activation after selection. Neither `AuthLoader` nor `GeminiAuthSelector` validates account status.

Legacy `[Cookies]` configuration remains supported. `GeminiAuthStateLoader.load_auth_state_with_fallback()` is retained as a deprecated compatibility path and is no longer part of the primary runtime selection flow.

## 10. Provider Routing Contract

The architecture enforces a "Thin Gateway" pattern with an encapsulated strategy layer:

```text
/v1/chat/completions
        ↓
ProviderFactory (Resolves logical identity, e.g., "gemini", "atlas")
        ↓
Provider (Logical Identity - e.g., GeminiProvider)
        ↓
Adapter (Execution Strategy - e.g., Playwright or WebAPI)
```

- **Ownership**: The endpoint handler is responsible for high-level routing via the factory.
- **Identity**: The Provider class represents the logical LLM vendor and owns all shared logic (e.g., tool parsing, prompt transformation) common to that vendor across different backends.
- **Strategy**: The Adapter encapsulates the technical implementation details of a specific execution backend (e.g., driving a browser via Playwright vs. using a REST API).
- **Transparency**: The gateway remains agnostic of whether a request is fulfilled via a browser-native runtime or a direct API client.

## 11. Future Evolution Policy

1. **Prioritize /v1**: All major architectural improvements must target the `/v1` namespace.
2. **Stability**: Legacy and Specialized endpoints must remain stable even if their underlying implementation is refactored.
3. **Contracts over Wrappers**: The structural API contracts defined here take precedence over any convenience wrappers or documentation summaries.
4. **Deprecation**: Removal of public endpoints should follow a documented deprecation process.
