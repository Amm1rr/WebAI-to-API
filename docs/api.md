# API Documentation

This document describes the public API surface exposed by WebAI-to-API.

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

---

### GET `/v1/models`

Returns the list of available models and providers.

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
| `playwright/` | Gemini Playwright |
| `atlas/`      | Atlas             |

Examples:

```text
gemini-3-flash
playwright/gemini-3-pro
atlas/MiniMax-M2
```

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
