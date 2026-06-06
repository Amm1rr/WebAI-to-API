# API Documentation

This document describes the public API surface exposed by WebAI-to-API.

Dashboard routes under `/ui/*` are administrative-only pages. They are excluded from the public API contract and from the OpenAPI schema.

## Base URL

```text
http://localhost:6969
```

---

## Primary API

### POST `/v1/chat/completions`

OpenAI-compatible chat completion endpoint.

#### Features

* Streaming and non-streaming responses
* Multi-provider routing
* Conversation continuation (provider-dependent)
* Standard OpenAI message format
* OpenAI-style multimodal `content` parts (`type: "text"` and `type: "file"`)
* System prompt support

#### Example

```json
{
  "model": "gemini-3-flash",
  "messages": [
    {
      "role": "user",
      "content": "Hello!"
    }
  ]
}
```

#### File Inputs

For Gemini WebAPI requests, `messages[].content` may be either:

* a plain string, or
* an array of content parts

Supported parts in the MVP:

* `{ "type": "text", "text": "..." }`
* `{ "type": "file", "file": { "filename": "...", "file_data": "data:...;base64,..." } }`

#### Supported Gemini WebAPI File Formats

Verified formats currently supported by WebAI for Gemini WebAPI file parts:

* `.pdf`
* `.doc`
* `.docx`
* `.txt`
* `.text`
* `.md`
* `.markdown`
* `.csv`
* `.log`
* `.png`
* `.jpg`
* `.jpeg`
* `.webp`
* `.gif`
* `.json`
* `.xml`
* `.xlsx`

File parts are Gemini WebAPI-only in the MVP. Remote URLs, filesystem paths, `file_id`, and unsupported content-part types are rejected. Backend validation remains authoritative.

For Gemini WebAPI, text content parts are concatenated into one prompt and file parts are passed as attachments, so exact text/file interleaving is not preserved.

Extensionless UTF-8 plain-text files are also accepted when their content passes text validation.

Current limits remain unchanged:

* 8 files
* 20 MiB per file
* 50 MiB total backend limit

See the same note in [docs/specs/api-contract.md](specs/api-contract.md) for the contract-level rules.

#### Generated Artifacts

Gemini WebAPI responses may include `choices[0].artifacts` in buffered responses. `message.content` remains text-only, and thoughts are not exposed.

Streaming responses may emit one final artifact SSE chunk before `[DONE]` with `choices[0].delta = {}` and `choices[0].artifacts = [...]`.

Artifacts are metadata only. Artifact blobs are not persisted.

Artifact URLs are opaque provider metadata and should not be assumed to be permanent, public, or to have stable download semantics.

---

### GET `/v1/models`

Returns the list of models exposed by registered providers.

The returned model list is registry-driven at runtime. Each registered provider contributes its available model IDs to this endpoint. Browser-native provider-aware namespaces may be used by registered browser providers, such as `playwright/<provider>/<model>`.

Legacy Gemini browser-native routing remains supported for backward compatibility using `playwright/<gemini-model>`.

---

### POST `/v1/temporary/chat/completions`

Gemini WebAPI-only OpenAI-compatible chat completion endpoint for temporary requests.

#### Features

* Streaming and non-streaming responses
* OpenAI-compatible request/response shape
* Gemini WebAPI direct requests use `temporary=True`
* No Gemini history persistence
* No durable `conversation_id` continuation
* Same multimodal file part and artifact behavior as `/v1/chat/completions`

#### Behavior

* `conversation_id` is rejected with HTTP 400
* `playwright/*` models are rejected with HTTP 400
* `atlas/*` models and `provider=atlas` are rejected with HTTP 400
* File parts are staged per request and cleaned up after completion
* Streaming responses still emit OpenAI-compatible SSE chunks and `[DONE]`
---

### GET `/v1/conversations`

Lists locally persisted Gemini WebAPI conversations stored in SQLite.

This endpoint supports Gemini WebAPI conversations only. It does not restore `ChatSession` objects, call Gemini remote APIs, or include Playwright URL-backed conversations or Atlas requests.

Successful response:

```json
{
  "object": "list",
  "provider": "gemini",
  "backend": "webapi",
  "count": 1,
  "data": [
    {
      "id": "conversation_id",
      "object": "conversation",
      "provider": "gemini",
      "backend": "webapi",
      "model": "gemini-3-flash",
      "gem_id": null,
      "updated_at": "2026-06-02T12:34:56+00:00",
      "schema_version": 1
    }
  ]
}
```

Status codes:

| Status | Meaning |
| ------ | ------- |
| `200` | Local SQLite snapshots were listed. |
| `503` | Session registry or snapshot repository is unavailable. |
| `500` | Snapshot data is invalid/corrupt or repository listing failed. |

---

### DELETE `/v1/conversations`

Best-effort deletes all locally persisted Gemini WebAPI conversations.

This endpoint lists local Gemini WebAPI SQLite snapshots, deletes each corresponding remote Gemini chat, and then deletes the local snapshot. Active conversations are skipped and reported. Playwright and Atlas conversations are not supported.

Successful response, including partial failures:

```json
{
  "object": "conversation.bulk_delete",
  "provider": "gemini",
  "backend": "webapi",
  "total": 3,
  "deleted_count": 1,
  "failed_count": 1,
  "skipped_active_count": 1,
  "results": [
    {
      "id": "deleted_conversation_id",
      "status": "deleted",
      "deleted": true
    },
    {
      "id": "active_conversation_id",
      "status": "skipped_active",
      "deleted": false,
      "error": "Conversation is currently in use"
    },
    {
      "id": "failed_conversation_id",
      "status": "failed",
      "deleted": false,
      "error": "Remote Gemini delete failed"
    }
  ]
}
```

Status codes:

| Status | Meaning |
| ------ | ------- |
| `200` | Bulk operation produced a report, even if individual conversations failed or were skipped. |
| `401` | Gemini WebAPI authentication is missing or expired before the run starts. |
| `503` | Gemini client, session registry, or snapshot repository is unavailable before the run starts. |
| `500` | Snapshot listing failed before a per-conversation report could be produced. |

---

### DELETE `/v1/conversations/{conversation_id}`

Deletes a Gemini WebAPI conversation identified by the local `conversation_id`.

This endpoint supports Gemini WebAPI conversations only. Gemini Playwright URL-backed conversations and Atlas requests are not supported by this delete endpoint.

Successful response:

```json
{
  "id": "conversation_id",
  "object": "conversation.deleted",
  "deleted": true,
  "provider": "gemini",
  "backend": "webapi"
}
```

Status codes:

| Status | Meaning |
| ------ | ------- |
| `200` | Remote Gemini delete and local cleanup completed. |
| `400` | Invalid `conversation_id`. |
| `401` | Gemini WebAPI authentication is missing or expired. |
| `404` | No local WebAPI snapshot exists for the `conversation_id`. |
| `409` | The conversation is active or already being deleted. |
| `503` | Gemini client or session registry is unavailable. |
| `500` | Remote Gemini deletion or local repository cleanup failed. |

---

## Authentication API

### GET `/v1/auth/status`

Returns the current authentication state and login status.

Authentication is provider-owned. `AuthLoader` discovers available auth material, provider auth strategies own selection and fallback policy, and `AuthManager` owns cached status plus login/recovery orchestration.
#### Optional Query Parameters

| Parameter | Description                         |
| --------- | ----------------------------------- |
| `refresh` | Forces a lightweight status refresh |

Example:

```text
GET /v1/auth/status?refresh=true
```

---

### POST `/v1/auth/login`

Starts an interactive browser-based login workflow.

#### Notes

* Requires a graphical desktop environment.
* Intended for host-based authentication.
* Not supported inside headless Docker containers.

For Docker deployments, use:

```bash
poetry run python verify_login.py
```

---

## System API

### GET `/health`

Process liveness endpoint.

Use this endpoint to determine whether the application process is running.

---

### GET `/ready`

Runtime readiness endpoint.

Indicates whether the application is structurally ready to serve requests.

---

### GET `/v1/runtime/status`

Provides runtime diagnostics and operational status information.

Useful for troubleshooting, monitoring, and operational visibility.

---

## Compatibility API

### POST `/v1beta/models/{model}`

Google Generative AI compatibility endpoint.

Supported actions:

* `generateContent`
* `streamGenerateContent`

This endpoint is intended for compatibility with integrations expecting the Google Generative AI API format.

---

## Legacy API

These endpoints are maintained for backward compatibility and are not recommended for new integrations.

### POST `/gemini`

Legacy stateless Gemini endpoint.

Each request is processed independently.

---

### POST `/gemini-chat`

Legacy conversation endpoint.

Conversation state is stored in memory and does not survive process restarts.

---

### POST `/translate`

Compatibility endpoint for Translate It! integrations.

Characteristics:

* Shared global session
* Gemini WebAPI requests are sent as temporary requests and are not saved in Gemini history
* Non-streaming responses
* No persistence across restarts

---

### GET `/v1/gems`

Returns available Gemini Gems associated with the authenticated account.

Returned Gem identifiers may be used in chat requests when supported by the selected backend.

---

## Provider Routing

Requests can be routed using model prefixes.

| Prefix        | Provider          |
| ------------- | ----------------- |
| *(none)*      | Gemini            |
| `playwright/<gemini-model>` | Gemini Playwright (legacy compatibility) |
| `playwright/<provider>/<model>` | Browser-native provider namespaces |
| `atlas/`      | Atlas             |

Examples:

```text
gemini-3-flash
playwright/<provider>/<model>
atlas/MiniMax-M2
```

Legacy Gemini browser routing using `playwright/<gemini-model>` remains supported for backward compatibility.
Legacy `playwright/<model>` routing is Gemini-only compatibility behavior. New browser-native providers should use provider-aware namespaces such as `playwright/<provider>/<model>`.
---

## Interactive Documentation

Swagger UI is available when the server is running.

```text
http://localhost:6969/docs
```

OpenAPI schema:

```text
http://localhost:6969/openapi.json
```
