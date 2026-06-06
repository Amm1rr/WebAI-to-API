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
| `/v1/conversations` | Primary | Yes | Lists/deletes Gemini WebAPI snapshots | No | GET lists local snapshots; DELETE bulk-deletes Gemini WebAPI conversations. |
| `/v1/conversations/{conversation_id}` | Primary | Yes | Deletes Gemini WebAPI snapshots | No | Gemini WebAPI-only conversation deletion. |
| `/v1/models` | Primary | Yes | N/A | No | Discovery endpoint for registered providers and their available model IDs. |
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

### 3.1 Multimodal Content Parts

`messages[].content` supports both plain strings and OpenAI-style content-part arrays.

- **Text**: `{ "type": "text", "text": "..." }`
- **File**: `{ "type": "file", "file": { "filename": "...", "file_data": "data:...;base64,..." } }`

Current MVP rules:

- Plain string content remains fully supported.
- Text parts are accepted and flattened into provider-specific prompt text.
- File parts are supported only for the Gemini WebAPI backend.
- File parts are request-scoped only. They are staged to server-owned temporary files for the current request and are not persisted in SQLite snapshots.
- Gemini Playwright and Atlas must reject file parts with a clear capability error.
- Remote URLs, filesystem paths, `file_id`, and unsupported content-part types are rejected.
- The currently verified file format list is maintained in [docs/api.md](../api.md).
- For Gemini WebAPI, text content parts are concatenated into one prompt and file parts are passed as attachments, so exact text/file interleaving order is not preserved.

### 3.2 Generated Output Artifacts

Gemini WebAPI may return generated artifacts alongside text output.

- **Buffered Responses**: Gemini WebAPI responses may include `choices[0].artifacts` while `message.content` remains text-only.
- **Streaming Responses**: Gemini WebAPI may emit one final SSE chunk before `[DONE]` that carries `choices[0].delta = {}` and `choices[0].artifacts = [...]`.
- **Provider Scope**: Generated output artifacts are Gemini WebAPI-specific. Playwright and Atlas do not expose this response shape.
- **Thoughts**: Model thoughts remain hidden by default and are not exposed through the public API response shape.
- **Persistence**: Artifact blobs are not persisted in local snapshots or conversation state.
- **Metadata Semantics**: Artifact URLs are provider metadata only. Clients must not assume they are permanent, public, or stable download handles.

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
- **Stateless providers**: This field may be absent or provider-defined because no local conversation state is maintained and no continuation can be guaranteed.

### Listing

`GET /v1/conversations` lists Gemini WebAPI conversations persisted in local SQLite snapshots only.

- **Gemini WebAPI**: The runtime reads SQLite snapshots through `SessionRegistry` and `SQLiteConversationRepository`, validates snapshot schema and provider-owned `session_state`, and returns public local metadata such as `conversation_id`, `updated_at`, `model_name`, `gem_id`, provider, backend, and schema version.
- **No Remote Calls**: Listing does not restore `ChatSession` objects and does not call Gemini remote APIs.
- **Metadata Privacy**: Raw Gemini continuation metadata and remote Gemini chat IDs are not exposed.
- **Gemini Playwright**: Not included because Playwright conversations are provider-side URL-backed and not SQLite-backed WebAPI snapshots.
- **Atlas**: Not included because Atlas requests are stateless in this runtime.

### Bulk Deletion

`DELETE /v1/conversations` best-effort deletes all locally persisted Gemini WebAPI conversations.

- **Gemini WebAPI**: The runtime lists SQLite snapshots through `SessionRegistry`, reserves each conversation with the per-conversation deletion tombstone, extracts the remote Gemini chat ID from `session_state.metadata[0]`, calls the Gemini WebAPI delete operation, then removes the local `SessionManager` and SQLite snapshot.
- **Best Effort**: The operation is not atomic. Individual active, remote-failed, or cleanup-failed conversations are reported per item while the endpoint continues processing remaining snapshots.
- **Status Semantics**: The endpoint returns `200 OK` whenever it can produce a bulk report, including partial failures. It does not use `207 Multi-Status`.
- **Concurrency**: Active or already deleting conversations are skipped with per-item status `skipped_active`; they are not force deleted and the endpoint does not wait for them.
- **Metadata Privacy**: Raw Gemini continuation metadata and remote Gemini chat IDs are not exposed in the response.
- **Gemini Playwright and Atlas**: Not supported by this endpoint.

### Single Deletion

`DELETE /v1/conversations/{conversation_id}` deletes Gemini WebAPI conversations only.

- **Gemini WebAPI**: The runtime reads the SQLite snapshot, extracts the remote Gemini chat ID from `session_state.metadata[0]`, calls the Gemini WebAPI delete operation, removes the in-memory `SessionManager`, and deletes the SQLite snapshot.
- **Gemini Playwright**: Not supported by this endpoint. Playwright conversation IDs are provider-side URL identifiers and are not SQLite-backed WebAPI snapshots.
- **Atlas**: Not supported because Atlas requests are stateless in this runtime.
- **Concurrency**: Active or already deleting conversations return `409 Conflict`.

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

- `AuthLoader` discovers available auth material.
- Provider auth strategies define source priority and fallback sequencing for their provider.
- `AuthManager` owns cached status returned by `/v1/auth/status` and coordinates login, status refresh, and provider-specific post-login recovery.

### 9.1 Conversation ID Semantics

`conversation_id` tokens are treated as **opaque tokens** that instruct the selected backend to attempt continuation using its native recovery mechanism.

- **Token Format**: All tokens generated by the system are cryptographically secure opaque strings. Implementation details (e.g., backend identity) are not encoded into the public ID.
- **WebAPI Continuity**: Identifies a locally persisted `ChatSession` snapshot in the SQLite repository.
- **Browser-native Continuity**: Uses provider-side conversation identifiers and URL-backed recovery mechanisms to attempt continuation of existing browser-native conversations.
- **Ownership Validation**: To prevent cross-backend routing errors, the system performs internal ownership validation:
  - If a `conversation_id` is found in the **SQLite repository**, it is strictly owned by the **WebAPI** backend.
  - Using a WebAPI-owned ID with a browser-native provider will return a `400 Bad Request`.
  - IDs not present in SQLite are not considered WebAPI-owned and are therefore eligible for browser-native continuation attempts.

Cross-backend conversation continuity between WebAPI and browser-native providers is not supported due to incompatible underlying state formats.

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
- **Browser-Native Routing**: Browser-native providers are selected through provider-aware model namespaces such as `playwright/<provider>/<model>`. Legacy Gemini browser routes using `playwright/<gemini-model>` remain supported for backward compatibility.

## 11. Future Evolution Policy

1. **Prioritize /v1**: All major architectural improvements must target the `/v1` namespace.
2. **Stability**: Legacy and Specialized endpoints must remain stable even if their underlying implementation is refactored.
3. **Contracts over Wrappers**: The structural API contracts defined here take precedence over any convenience wrappers or documentation summaries.
4. **Deprecation**: Removal of public endpoints should follow a documented deprecation process.

## 12. System and Runtime Endpoints

The system exposes dedicated endpoints for health monitoring and runtime observability.

### `/health` (Liveness)
- **Purpose**: Indicates if the Python process is alive and responsive.
- **Semantics**: Returns `200 OK` if the app is running and not in a terminal shutdown state.
- **Safety**: Strictly side-effect-free. Does not bootstrap the `BrowserEngine`.

### `/ready` (Readiness)
- **Purpose**: Indicates if the structural runtime is capable of accepting and processing browser-native requests.
- **Semantics**: Returns `200 OK` if the `BrowserEngine` is initialized, the browser process is connected, and at least one `ProviderSession` is structurally alive.
- **Exclusion**: **Does not validate authentication**. A node is considered structurally ready even if authentication is expired.
- **Safety**: Side-effect-free. Does not trigger recovery or browser launches.

### `/v1/runtime/status` (Diagnostics)
- **Purpose**: Provides deep observability into the hardened runtime's internal state.
- **Payload**: Includes engine generation, browser connectivity, active lease counts, registry sizes, and cached authentication summary.
- **Safety**: Strictly side-effect-free. Does not refresh authentication or trigger recovery.
