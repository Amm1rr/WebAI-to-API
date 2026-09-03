# ADR-0001: Client-Owned Stateless Chat Execution

> **Status:** Accepted
> **Date:** 2026-08-29
> **Implementation:** Implemented for direct Gemini WebAPI

## Context

WebAI-to-API currently exposes `/v1/chat/completions` as its primary
OpenAI-compatible API.

Conversation ownership on that endpoint is backend-dependent:

* Gemini WebAPI can persist and restore conversation state.
* Gemini Playwright can continue provider-side conversations through
  `conversation_id` and `PersistentTab`.
* Stateless providers may execute requests independently.
* `/v1/temporary/chat/completions` historically provided a separate Gemini
  WebAPI-only temporary mode; it is now a deprecated backward-compatibility
  wrapper that delegates to the canonical stateless implementation
  (see Existing Temporary Endpoint).

This model works for clients that want WebAI-to-API to own conversation
continuity.

Agent runtimes such as Hermes Agent use a different model: the client owns the
complete conversation history and sends the required history again on every
model request, including assistant tool calls and tool results.

Using the stateful API for such clients creates two independent sources of
conversation state:

1. client-side history;
2. provider/backend conversation history.

That duplication complicates retries, context compression, tool execution,
browser recovery, model switching, failover, and deterministic replay.

A generic stateless execution contract is therefore required.

## Decision

WebAI-to-API provides a dedicated OpenAI-compatible stateless API surface:

```text
GET  /v1/stateless/models
POST /v1/stateless/chat/completions
```

The defining rule is:

> The client owns conversation state. Each stateless request is self-contained
> and MUST NOT depend on conversational state created by a previous stateless
> request.

`stateless` describes conversation ownership, not client-generation lifecycle.

## Implemented Architecture

The implemented stateless route:

* accepts direct Gemini WebAPI models only;
* sends the full client-supplied message history on every request;
* executes Gemini WebAPI with `temporary=True`;
* rejects `conversation_id`;
* does not restore or create `SessionRegistry` state or SQLite conversation
  snapshots;
* supports buffered responses, progressive SSE, and tool-compatible responses;
* keeps the stateful `/v1/chat/completions` API separate.

Execution-resource leases may be used to protect the active Gemini client, but
they do not carry conversation state between stateless requests.

## Conversation Ownership

For stateless requests:

* the client MUST send all conversation context required for the current
  request;
* `conversation_id` MUST NOT be accepted as a continuation mechanism;
* WebAI-to-API MUST NOT require a previous stateless request to complete the
  current request;
* retries MUST be safe from hidden WebAI-managed conversation continuation;
* tool-call continuation MUST be reconstructed from messages supplied by the
  client.

The existing `/v1/chat/completions` contract remains the stateful,
provider-dependent API surface.

## Gemini WebAPI

Gemini WebAPI stateless execution uses its temporary request capability.

Required implemented semantics:

* execute with `temporary=True`;
* do not create SQLite conversation snapshots;
* do not reuse a `ChatSession` from a previous stateless request;
* do not persist the request into Gemini conversation history;
* process the complete supplied OpenAI message history;
* support applicable OpenAI-compatible buffered, streaming, and tool-call
  response shapes.

## Tool Calling

Tool execution remains client-owned.

For every stateless tool iteration:

1. the client sends messages and available tools;
2. the backend returns an OpenAI-compatible `tool_calls` result;
3. the client executes the tool;
4. the client appends the assistant tool call and tool result to its own
   history;
5. the client sends a new self-contained stateless request.

The backend supports one generated function tool call per response. The
function name must be a non-empty string and must match a function declared in
the current request. Its arguments must be a JSON object. OpenAI responses
expose those arguments as a JSON string. Multiple generated tool calls are
unsupported, and declared function parameter JSON Schema is not independently
validated.

Historical assistant tool calls must contain unique IDs, `type: "function"`, a
function name that is a non-empty string, and JSON-string arguments with an
object root. Each tool
result must reference an existing pending call ID and consume it once. Multiple
historical calls, reverse result order, and historical names absent from the
current `tools` declaration are supported. Malformed, orphan, duplicate, or
unresolved associations return HTTP 422.

## Streaming

Stateless chat uses OpenAI-compatible SSE.

For `stream=true` without tools:

* stream `id`, `created`, and `model` remain stable;
* content chunks use `finish_reason: null`;
* one successful terminal chunk uses `delta: {}` and
  `finish_reason: "stop"`;
* `[DONE]` follows the terminal chunk;
* an artifact chunk, when present, is the terminal stop chunk.

If a provider failure or timeout occurs after SSE headers are sent, the stream
ends without a terminal stop chunk and without `[DONE]`.

For `stream=true` with tools, generation is buffered first and then replayed as
OpenAI-compatible SSE. The tool chunk uses `delta.tool_calls`, `index: 0`, and
`finish_reason: "tool_calls"`, followed by `[DONE]`. This is not native
progressive tool streaming.

## Request Controls and Errors

The implemented request-control policy is:

| Control | Stateless behavior |
| --- | --- |
| `max_tokens`, `max_completion_tokens` | Accepted, no effect |
| `reasoning_effort` | Accepted, no effect |
| `stream_options.include_usage` | Accepted, no effect |
| `temperature`, `top_p`, `top_k` | HTTP 400 unsupported |
| `response_format`, `parallel_tool_calls` | HTTP 400 unsupported |
| `tool_choice` | HTTP 400 unsupported |

Malformed values, invalid types or ranges, and both token aliases return HTTP
422. `provider_options.gemini.extended_thinking` is rejected with HTTP 400;
stateful Gemini provider options do not automatically apply to this surface.

Direct Gemini WebAPI execution has a 300-second request deadline. The public
status mapping is:

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

## Model Discovery

`GET /v1/stateless/models` advertises only currently available direct Gemini
WebAPI models. It does not mirror `/v1/models` and does not advertise
Playwright, Atlas, legacy browser aliases, or other unsupported models.

## Known Limitations and Deferred Scope

The following are not implemented by this decision:

* Playwright stateless execution;
* generated multiple tool calls;
* `parallel_tool_calls`;
* `tool_choice`.

Phase 4C client-disconnect cancellation and Phase 4D shared-client recovery
isolation remain tracked limitations outside this ADR. This decision does not
claim either capability.

## Existing Temporary Endpoint

`/v1/temporary/chat/completions` remains as a deprecated backward-compatibility
wrapper. It delegates to the canonical stateless implementation
(`POST /v1/stateless/chat/completions`) and therefore preserves the same
Gemini WebAPI execution semantics: `temporary=True`, no SQLite snapshot, and
no Gemini conversation-history persistence. The generic stateless API does not
redefine or weaken that contract.

Current architecture:

```text
/v1/stateless/chat/completions
    = canonical client-owned-history Gemini WebAPI endpoint

/v1/temporary/chat/completions
    = deprecated backward-compatibility wrapper
```

## Compatibility Non-Goals

The stateless API does not emulate unrelated native server protocols solely to
satisfy discovery probes. Generic OpenAI-compatible integrations should use
the documented model and chat surfaces.

## Consequences

### Positive

* one authoritative owner of agent conversation state;
* deterministic tool loops;
* safer retries and replay;
* cleaner compatibility with agent runtimes;
* stateless execution remains separate from stateful conversation sessions.

### Costs

* full conversation history must be serialized for each stateless request;
* stateless model capability is tracked independently from the general model
  catalog;
* direct requests may still share upstream client infrastructure.

## Non-Goals

This ADR does not:

* change `/v1/chat/completions`;
* remove `/v1/temporary/chat/completions`;
* change existing stateful `conversation_id` semantics;
* make `PersistentTab` stateless;
* require Playwright support;
* guarantee provider-side history deletion for browser-native backends;
* require native Ollama, LM Studio, llama.cpp, or vLLM protocol emulation.
