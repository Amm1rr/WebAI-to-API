# API Documentation

This document describes the public API surface exposed by WebAI-to-API.

Dashboard routes under `/ui/*` are administrative-only pages. They are excluded from the public API contract and from the OpenAPI schema.

## Base URL

```text
http://localhost:6969
```

This is the default local host URL. Docker keeps the application port at
`6969`; set `WEB_PORT` to change only the host-facing port and use that port
in client URLs.

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

#### OpenAI Request Controls

Audited OpenAI controls are parsed and validated before backend execution. Invalid values and simultaneous
`max_tokens` plus `max_completion_tokens` return HTTP 422. An explicitly supplied control that the selected backend
does not support returns HTTP 400; controls are never silently forwarded as if supported.

| Control | Gemini WebAPI | Gemini Playwright | Atlas |
| --- | --- | --- | --- |
| `max_tokens` | Accepted, no effect | Accepted, no effect | 400, not forwarded |
| `max_completion_tokens` | Accepted, no effect | Accepted, no effect | 400, not forwarded |
| `reasoning_effort` | Accepted, no effect | Accepted, no effect | 400, not forwarded |
| `stream_options.include_usage` | Accepted, no effect | Accepted, no effect | 400, not forwarded |
| `temperature` | 400 | 400 | 400, not forwarded |
| `top_p` | 400 | 400 | 400, not forwarded |
| `top_k` | 400 | 400 | 400, not forwarded |
| `response_format` | 400 | 400 | 400, not forwarded |
| `parallel_tool_calls` | 400 | 400 | 400, not forwarded |
| `tool_choice` | 400 | 400 | Forwarded unchanged |

Gemini compatibility no-ops do not alter generation settings, usage output, extended-thinking behavior, or
conversation persistence. `reasoning_effort` is not mapped to `provider_options.gemini.extended_thinking`.

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

#### Gemini Extended Thinking

This option applies to stateful `/v1/chat/completions` requests. The stateless and temporary Gemini WebAPI endpoints reject `provider_options.gemini` with HTTP 400; stateful Gemini support does not extend to those surfaces.

Gemini WebAPI and Playwright requests may control Extended thinking through typed provider options:

```json
{
  "model": "playwright/gemini-3.5-flash",
  "messages": [
    {"role": "user", "content": "Solve this problem"}
  ],
  "provider_options": {
    "gemini": {
      "extended_thinking": true
    }
  }
}
```

When request option is omitted, effective value comes from `[Gemini].extended_thinking`; missing key falls back to `false`. Explicit `true` or `false` overrides config. The option is request-scoped: reused conversations may switch values between turns, and Playwright requests do not inherit prior persistent-tab UI state. WebAPI passes the resolved value to upstream chat generation. Atlas rejects the option with HTTP 400. Unknown provider namespaces/options and invalid types return HTTP 422. This option is not equivalent to `reasoning_effort`.

Do not use this form. `extended_thinking` must be nested under `provider_options.gemini`; a top-level `extended_thinking` field is not part of the API contract and may be ignored.

```json
{"extended_thinking": true}
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

> [!NOTE]
> Atlas models are only advertised when Atlas is configured with a valid API key. If Atlas is not configured, Atlas models will not appear in the model catalog.

Legacy Gemini browser-native routing remains supported for backward compatibility using `playwright/<gemini-model>`.

---

## Stateless Chat API

The stateless surface is intended for Hermes Agent and other clients that own conversation history.

### GET `/v1/stateless/models`

Returns only currently available direct Gemini WebAPI models that satisfy the stateless execution contract, including valid slash-containing model IDs when advertised by the Gemini WebAPI runtime catalog. The catalog is the authority; every model returned here is accepted by `/v1/stateless/chat/completions` under the same runtime state.

Atlas models, Playwright models, legacy Playwright aliases, and models unavailable to the direct WebAPI backend are not advertised. Playwright stateless execution is not implemented.

### POST `/v1/stateless/chat/completions`

Canonical stateless Gemini WebAPI endpoint. OpenAI-compatible chat completion where the client owns conversation history.

#### Backend and state

* Gemini WebAPI is the only supported backend.
* Playwright stateless execution is not implemented.
* Atlas and other non-Gemini providers are not supported on this surface.
* Every request uses Gemini WebAPI `temporary=True` execution.
* The client owns conversation history and must send the complete history required for each request, including `system`, `user`, `assistant`, and `tool` messages.
* `conversation_id` is rejected with HTTP 400. No server continuation ID is created or returned.
* Requests do not use server conversation continuation or SQLite conversation snapshots.
* Slash-containing model IDs are valid when advertised by `/v1/stateless/models` and recognized as available by the runtime Gemini catalog; unknown slash IDs are rejected. Slash does not imply provider routing. `playwright/*` and `atlas/*` remain rejected.

#### Request controls

These controls are accepted for OpenAI client compatibility but have no effect on Gemini WebAPI generation:

| Control | Behavior |
| --- | --- |
| `max_tokens` | Accepted, no effect |
| `max_completion_tokens` | Accepted, no effect |
| `reasoning_effort` | Accepted, no effect |
| `stream_options.include_usage` | Accepted, no effect |

These controls are unsupported and return HTTP 400:

| Control | Behavior |
| --- | --- |
| `temperature` | Unsupported |
| `top_p` | Unsupported |
| `top_k` | Unsupported |
| `response_format` | Unsupported |
| `parallel_tool_calls` | Unsupported |
| `tool_choice` | Unsupported |

Malformed declared values, invalid types or ranges, and sending both `max_tokens` and `max_completion_tokens` return HTTP 422. Accepted no-effect controls are not forwarded and do not produce fake usage data.

#### Extended thinking

`provider_options.gemini.extended_thinking` is rejected with HTTP 400 on this endpoint. Do not infer stateless support from the broader stateful Gemini WebAPI option.

#### Buffered responses

With `stream=false` (the default), successful text responses use the OpenAI-compatible `chat.completion` shape. Normal text uses `finish_reason: "stop"`. Stateless responses do not include `usage`, `conversation_id`, or `reused_conversation`.

#### Progressive streaming

With `stream=true` and no tools, the endpoint emits Server-Sent Events containing `chat.completion.chunk` objects:

* `id`, `created`, and `model` remain stable for the stream.
* Content chunks use `delta.content` and `finish_reason: null`.
* Successful completion emits exactly one terminal chunk with `delta: {}` and `finish_reason: "stop"`.
* The terminal chunk is followed by `data: [DONE]`.
* If the response includes generated artifacts, the artifact chunk is the terminal `"stop"` chunk; no additional empty terminal chunk is emitted.

If a provider failure or timeout occurs after SSE headers are sent, the stream terminates without a terminal `"stop"` chunk and without `[DONE]`. This is an incomplete response, not successful completion.

Direct Gemini WebAPI execution has a 300-second request deadline covering buffered generation and progressive stream generation.

#### Generated tool calls

The current provider contract supports one model-generated function tool call per response:

* The function name must be a non-empty string and must match a function declared in the current request's `tools`.
* Arguments must be a JSON object.
* OpenAI responses expose arguments as a JSON string.
* Malformed client tool declarations return HTTP 422; malformed provider tool output returns HTTP 502.
* Multiple generated tool calls are unsupported.

Tool parameter JSON Schema is provided to the model but is not independently validated by this endpoint.

#### Tool-buffered streaming

`stream=true` with `tools` buffers provider generation first, then emits an OpenAI-compatible SSE replay. This is not native progressive tool streaming. The tool chunk contains `delta.tool_calls`, uses `index: 0`, and has `finish_reason: "tool_calls"`, followed by `[DONE]`.

#### Tool history

For client-owned tool loops, historical assistant tool calls must include:

* a unique non-empty call ID;
* `type: "function"`;
* The function name must be a non-empty string.
* JSON-string arguments whose root value is an object.

Each historical tool result must reference an existing pending call ID and may consume that ID only once. Multiple calls are supported; results may arrive in a different order from their declarations; historical function names do not need to appear in the current request's `tools`.

Malformed, orphan, duplicate, or unresolved tool associations return HTTP 422.

#### Error and status mapping

| Case | Status |
| --- | ---: |
| Unsupported capability, provider, or backend | 400 |
| Invalid request or tool history | 422 |
| Usage limit or temporary provider block | 429 |
| Gemini unavailable or authentication not ready | 503 |
| Direct Gemini timeout | 504 |
| Expected upstream/provider failure | 502 |
| Malformed provider tool output | 502 |
| Unexpected server defect | 500 |

#### Known limitations

* Playwright stateless execution is not implemented.
* Generated multiple tool calls are unsupported.
* `parallel_tool_calls` and `tool_choice` are unsupported.
* Client disconnect or cancellation may not immediately abort the underlying curl transfer.
* Concurrent requests share upstream Gemini WebAPI client infrastructure; provider-level recovery and failure isolation are not guaranteed.

---

### POST `/v1/temporary/chat/completions`

Deprecated compatibility endpoint. New integrations must use `POST /v1/stateless/chat/completions`. This endpoint remains for backward compatibility and delegates to the same canonical stateless Gemini WebAPI implementation (`temporary=True`, client-owned history, no `conversation_id`, no SQLite snapshots).

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
* Successful streaming responses emit OpenAI-compatible SSE chunks and `[DONE]`; terminally truncated streams may end without `[DONE]`
* Marked `deprecated=True` in OpenAPI; prefer `/v1/stateless/chat/completions`
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

* Stateless per-request execution through the shared authenticated Gemini client
* Gemini WebAPI requests use `temporary=True` and are not saved in Gemini history
* No conversation state
* Independent requests can execute concurrently at the application layer; dependency, network, and Gemini remote limits still apply
* Non-streaming responses

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

Technical API specifications and interactive testing surfaces:

- **Dashboard Catalog**: [http://localhost:6969/ui/apis](http://localhost:6969/ui/apis) (User-friendly catalog with feature badges)
- **Swagger UI**: [http://localhost:6969/docs](http://localhost:6969/docs) (Interactive testing and schema inspection)
- **ReDoc**: [http://localhost:6969/redoc](http://localhost:6969/redoc) (Clean, three-panel documentation)
- **OpenAPI Schema**: [http://localhost:6969/openapi.json](http://localhost:6969/openapi.json)
