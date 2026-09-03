# Stateless Chat Execution Contract

> **Status:** Implemented
> **Scope:** `/v1/stateless/*`
> **Authority:** This specification defines the implemented stateless execution contract. The current surface supports direct Gemini WebAPI execution only.

## 1. Purpose

The stateless chat API serves clients that own conversation history, including
Hermes Agent and other autonomous OpenAI-compatible runtimes.

The defining invariant is:

> Every request contains all conversational state required to execute that request.

No previous stateless request may be required for semantic continuation.

## 2. Implemented Endpoint Surface

```text
GET  /v1/stateless/models
POST /v1/stateless/chat/completions   canonical
POST /v1/temporary/chat/completions   deprecated compatibility wrapper
```

The surface is OpenAI Chat Completions-compatible within the limits described
here. `GET /v1/stateless/models` advertises only currently available direct
Gemini WebAPI models that can execute through this contract, including valid
slash-containing model IDs when the runtime catalog reports them as available.

Every model returned by `GET /v1/stateless/models` is accepted by
`POST /v1/stateless/chat/completions` under the same runtime state.

The stateless surface does not support:

* Gemini Playwright execution;
* Atlas execution;
* other non-Gemini providers;
* server-owned conversation continuation.

## 3. State Ownership and Execution

### 3.1 Client-owned history

The client owns and must resend all history required for each request:

* system instructions;
* user messages;
* assistant messages;
* assistant tool calls;
* tool results;
* any context compression or replay decisions.

The backend may transform the supplied messages into a Gemini prompt, but it
must not depend on history from an earlier stateless request.

### 3.2 Gemini WebAPI execution

Every stateless request uses direct Gemini WebAPI execution with
`temporary=True`. The request does not restore or create a server conversation
session, a SQLite conversation snapshot, or a Gemini conversation-history
continuation.

`conversation_id` is rejected with HTTP 400. The endpoint neither creates nor
returns a continuation ID.

The stateful `/v1/chat/completions` API remains separate. Its provider/backend
conversation-continuation behavior is not inherited by this surface.

### 3.3 Model ID validity

Slash-containing model IDs are valid when the active Gemini WebAPI runtime
model catalog/resolver reports them as available. The catalog is the sole
authority; slash does not imply provider routing and does not bypass
availability checks. Unknown slash IDs, `playwright/*`, and `atlas/*` are
rejected. The invariant

> Every model advertised by `GET /v1/stateless/models` is accepted by
> `POST /v1/stateless/chat/completions` under the same runtime state

is maintained via shared classification logic between model advertisement and
request validation.

## 4. Request Compatibility

### 4.1 Accepted no-op controls

The following controls are accepted for OpenAI client compatibility but do not
change Gemini WebAPI generation:

| Control | Behavior |
| --- | --- |
| `max_tokens` | Accepted, no effect |
| `max_completion_tokens` | Accepted, no effect |
| `reasoning_effort` | Accepted, no effect |
| `stream_options.include_usage` | Accepted, no effect |

These controls are not forwarded as Gemini generation settings and do not
synthesize usage output.

### 4.2 Unsupported controls

The following controls return HTTP 400 when supplied:

| Control | Behavior |
| --- | --- |
| `temperature` | Unsupported |
| `top_p` | Unsupported |
| `top_k` | Unsupported |
| `response_format` | Unsupported |
| `parallel_tool_calls` | Unsupported |
| `tool_choice` | Unsupported |

Malformed values, invalid types or ranges, and supplying both
`max_tokens` and `max_completion_tokens` return HTTP 422 request-validation
errors. Schema acceptance alone does not make a control semantically
supported.

### 4.3 Extended thinking

`provider_options.gemini.extended_thinking` is rejected with HTTP 400 on the
stateless endpoint. The stateful Gemini WebAPI provider option does not apply
automatically to stateless or temporary requests.

## 5. Response Contract

### 5.1 Buffered responses

With `stream=false` or an omitted `stream` field, a successful normal text
response is an OpenAI-compatible `chat.completion` object:

* normal text uses `finish_reason: "stop"`;
* no fake `usage` object is added;
* `conversation_id` is absent;
* `reused_conversation` is absent.

Generated tool calls and artifacts follow their sections below.

### 5.2 Progressive streaming

With `stream=true` and no tools, the endpoint emits SSE
`chat.completion.chunk` objects.

For one successful stream:

* `id`, `created`, and `model` remain stable across all chunks;
* content chunks use `delta.content`;
* content chunks use `finish_reason: null`;
* exactly one terminal chunk uses `delta: {}` and `finish_reason: "stop"`;
* `data: [DONE]` follows the terminal chunk.

If generated artifacts exist, the artifact chunk is the terminal `"stop"`
chunk and carries `delta: {}` plus the artifact metadata. No additional empty
terminal chunk is emitted.

If a provider failure or timeout occurs after SSE headers are sent, the stream
ends without a terminal `"stop"` chunk and without `[DONE]`. Such a stream is
incomplete and must not be treated as successful completion.

### 5.3 Tool-buffered streaming

When `stream=true` and `tools` are supplied, the endpoint buffers provider
generation first and then emits an OpenAI-compatible SSE replay. This is not
native progressive tool streaming.

The generated tool chunk contains:

* `delta.tool_calls`;
* `index: 0`;
* `finish_reason: "tool_calls"`;
* followed by `data: [DONE]`.

## 6. Tool Contract

### 6.1 Generated tool calls

The implementation supports one model-generated function tool call per
response.

The generated call must:

* use a function name that is a non-empty string and matches a function declared in the current request's `tools`;
* contain arguments whose root value is a JSON object.

The OpenAI response exposes `function.arguments` as a JSON string. Malformed
client tool declarations return HTTP 422; malformed provider tool output
returns HTTP 502. Multiple generated tool calls are unsupported. Declared
function parameter JSON Schema is passed to the model, but arguments are not
independently validated against that schema.

### 6.2 Client-owned tool history

Historical assistant tool calls must include:

* a unique, non-empty call ID;
* `type: "function"`;
* the function name must be a non-empty string;
* JSON-string arguments whose root value is an object.

Each historical tool result must reference an existing pending call ID and may
consume that ID only once.

The history contract allows:

* multiple calls in one assistant tool-call group;
* tool results in an order different from call declarations;
* historical function names that are absent from the current request's
  `tools`.

Malformed, orphan, duplicate, or unresolved tool associations return HTTP 422.
The next request in a tool loop must include the prior assistant tool call and
its tool result in the client-owned history.

## 7. Timeout and Error Contract

Direct Gemini WebAPI execution has a 300-second request deadline covering
buffered generation and progressive stream generation.

For pre-header request failures, the stateless endpoint uses this mapping:

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

The endpoint must not silently fall back to stateful conversation continuation
when stateless validation or execution fails.

## 8. Concurrency and Resource Boundary

Stateless direct requests acquire an application-level Gemini generation lease.
Buffered requests release it after generation and response construction.
Progressive streams transfer lease cleanup to the streaming response boundary,
which releases it after normal completion, timeout, failure, cancellation, or
response-start failure.

The lease protects client-generation lifecycle ownership; it does not create or
represent a conversation. Stateless requests remain conversation-independent
even when they use the same application-level client infrastructure.

No stateless request acquires stateful conversation continuation through
`SessionRegistry`, a SQLite snapshot, or a persistent conversation session.

## 9. Known Limitations and Deferred Scope

These are limitations, not current requirements or implemented capabilities:

* Playwright stateless execution is not implemented.
* Generated multiple tool calls are unsupported.
* `parallel_tool_calls` is unsupported.
* `tool_choice` is unsupported.
* Client disconnect or cancellation may not immediately abort the underlying
  curl transfer (Phase 4C).
* Concurrent requests share upstream Gemini WebAPI client infrastructure;
  provider-level recovery and failure isolation are not guaranteed (Phase 4D).

## 10. Relationship to Existing APIs

### `/v1/chat/completions`

The primary provider-aware API. Conversation continuity and supported controls
remain backend-dependent.

### `/v1/temporary/chat/completions`

Deprecated compatibility wrapper. New integrations must use the canonical
`POST /v1/stateless/chat/completions`. The temporary endpoint delegates to the
same stateless Gemini WebAPI implementation (`temporary=True`, client-owned
history, no `conversation_id`, no SQLite snapshots), is marked `deprecated=True`
in OpenAPI, and is retained only for backward compatibility. It shares the
response shape, streaming, tool, multimodal, and timeout behavior with the
canonical endpoint.

The stateless endpoint must not weaken or silently redefine either existing
API. The canonical ownership is:

```text
stateless implementation
    ↑
    ├── /v1/stateless/chat/completions
    └── /v1/temporary/chat/completions   deprecated wrapper
```

## 11. Fundamental Invariants

1. The client owns stateless conversation history.
2. Every stateless request is independently executable from its supplied input.
3. Direct stateless execution uses Gemini WebAPI `temporary=True`.
4. Stateless requests do not create WebAI-owned conversation continuation state.
5. `conversation_id` is rejected on the stateless chat endpoint.
6. Accepted compatibility no-ops do not alter Gemini generation or synthesize usage.
7. Unsupported controls return HTTP 400; malformed request or tool history returns HTTP 422.
8. Successful progressive streams emit one terminal stop chunk followed by `[DONE]`.
9. Terminally failed progressive streams do not emit a false terminal stop or `[DONE]`.
10. Tool-loop continuation is reconstructed only from client-supplied history.
