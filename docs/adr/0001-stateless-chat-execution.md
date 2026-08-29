# ADR-0001: Client-Owned Stateless Chat Execution

> **Status:** Accepted
> **Date:** 2026-08-29
> **Implementation:** Pending

## Context

WebAI-to-API currently exposes `/v1/chat/completions` as its primary OpenAI-compatible API.

Conversation ownership on that endpoint is backend-dependent:

* Gemini WebAPI can persist and restore conversation state.
* Gemini Playwright can continue provider-side conversations through `conversation_id` and `PersistentTab`.
* Stateless providers may execute requests independently.
* `/v1/temporary/chat/completions` provides a separate Gemini WebAPI-only temporary mode.

This model works for clients that want WebAI-to-API to own conversation continuity.

Agent runtimes such as Hermes Agent use a different model: the client owns the complete conversation history and sends the required history again on every model request, including assistant tool calls and tool results.

Using the stateful API for such clients creates two independent sources of conversation state:

1. client-side history;
2. provider/backend conversation history.

That duplication complicates retries, context compression, tool execution, browser recovery, model switching, failover, and deterministic replay.

A generic stateless execution contract is therefore required.

## Decision

WebAI-to-API will introduce a provider-aware stateless OpenAI-compatible API surface:

```text
GET  /v1/stateless/models
POST /v1/stateless/chat/completions
```

The defining rule is:

> The client owns conversation state. Each stateless request is self-contained and MUST NOT depend on conversational state created by a previous stateless request.

`stateless` describes **conversation ownership**, not browser resource lifetime.

Execution resources MAY be reused when reuse cannot carry conversational state across requests.

## Conversation Ownership

For stateless requests:

* the client MUST send all conversation context required for the current request;
* `conversation_id` MUST NOT be accepted as a continuation mechanism;
* WebAI-to-API MUST NOT require a previous stateless request to complete the current request;
* retries MUST be safe from hidden WebAI-managed conversation continuation;
* tool-call continuation MUST be reconstructed from the messages supplied by the client.

The existing `/v1/chat/completions` contract remains the stateful/provider-dependent API surface.

## Gemini WebAPI

Gemini WebAPI stateless execution MUST use its temporary request capability.

Required semantics:

* execute with `temporary=True`;
* do not create SQLite conversation snapshots;
* do not reuse a `ChatSession` from a previous stateless request;
* do not persist the request into Gemini conversation history;
* process the complete supplied OpenAI message history;
* support the same applicable OpenAI-compatible streaming and tool-call response shapes.

This backend provides both local and provider-side temporary semantics.

## Gemini Playwright

Playwright stateless execution will use a different mechanism because browser-native Gemini does not provide the same `temporary=True` capability.

Required semantics:

* each request MUST begin from a fresh Gemini conversation state;
* complete client-supplied history MUST be serialized into the request;
* previous Gemini conversation context MUST NOT be reused;
* stateless requests MUST NOT acquire continuity through `conversation_id`;
* stateless execution MUST NOT register its pages as conversation-owned `PersistentTab` instances.

Playwright MAY reuse an already-loaded browser `Page` for performance.

Therefore:

```text
Stateless conversation
!=
Disposable browser page
```

The desired browser architecture is:

```text
Hermes / Client
      owns history
          |
          v
Stateless Request
          |
          v
Warm Page Lease
          |
          v
Reset to Fresh Gemini Chat
          |
          v
Execute Full Self-Contained Prompt
          |
          v
Clean Request State
          |
          v
Return Page to Warm Pool
```

## Warm Page Pool

Future Playwright stateless execution SHOULD use a dedicated warm-page pool.

This pool MUST remain separate from the existing persistent conversation registry.

A warm stateless page:

* has no public `conversation_id` ownership;
* MUST NOT represent a persistent Gemini conversation;
* MAY survive multiple API requests;
* MUST be exclusively leased to one request at a time;
* MUST be reset to a verified fresh-chat state before accepting another request;
* MUST have request-scoped observers, callbacks, listeners, and generation state cleaned before reuse;
* MUST be invalidated rather than reused after uncertain or poisoned state;
* MUST remain generation-aware and obey BrowserEngine shutdown and recovery contracts.

`PersistentTab` continues to represent stateful conversation continuity and MUST NOT be overloaded to also represent stateless warm pages.

## Fresh-Chat Reset

The initial correctness-first reset strategy for Playwright SHOULD be navigation to the canonical Gemini new-chat surface.

Conceptually:

```text
existing warm page
    -> navigate/reset to fresh Gemini chat
    -> verify clean input state
    -> configure model/options
    -> execute request
```

A faster DOM-based or SPA `New Chat` action MAY replace navigation later only after its reset semantics and reliability are independently verified.

Performance optimization MUST NOT weaken conversation isolation.

## Tool Calling

Tool execution remains client-owned.

For every stateless tool iteration:

1. client sends messages and available tools;
2. backend returns an OpenAI-compatible `tool_calls` result;
3. client executes the tool;
4. client appends the assistant tool call and tool result to its own history;
5. client sends a new self-contained stateless request.

The backend MUST NOT require reuse of the Gemini conversation that produced the original tool call.

This rule applies even when Playwright internally reuses a warm browser page.

## Streaming

Stateless chat uses the normal OpenAI-compatible SSE contract.

Backend implementation MAY use:

* progressive streaming;
* buffered generation followed by compatible SSE replay when required for tool-call parsing.

The public response contract MUST remain backend-independent where practical.

## Model Discovery

`GET /v1/stateless/models` MUST advertise only models that are actually usable through the stateless endpoint.

The stateless catalog MUST NOT blindly mirror `/v1/models`.

Initial implementation may expose Gemini WebAPI models only.

Playwright models MAY be added when Playwright stateless execution satisfies this ADR.

## Provider-Side History

Stateless means WebAI-to-API does not reuse conversational state between requests.

It does not universally mean that every upstream provider leaves no server-side history.

Backend guarantees MUST be documented separately:

| Backend           | WebAI Conversation State | Provider-Side History                     |
| ----------------- | ------------------------ | ----------------------------------------- |
| Gemini WebAPI     | None                     | Not persisted when using `temporary=True` |
| Gemini Playwright | None                     | May create Gemini-side history            |
| Future backends   | None                     | Backend-dependent                         |

Playwright MUST NOT claim the stronger Gemini WebAPI temporary-history guarantee unless such behavior becomes independently available and verified.

## Existing Temporary Endpoint

`/v1/temporary/chat/completions` remains a Gemini WebAPI-specific specialized endpoint.

Its stronger guarantee remains unchanged:

* Gemini WebAPI only;
* `temporary=True`;
* no SQLite snapshot;
* no Gemini conversation-history persistence.

The generic stateless API MUST NOT silently redefine or weaken this contract.

## Compatibility Probes

The stateless API MUST NOT emulate unrelated native server protocols solely to satisfy discovery probes.

In particular, WebAI-to-API must not add fake compatibility endpoints such as:

```text
/api/tags
/api/show
/props
/version
```

unless the project deliberately implements the corresponding protocol.

Generic OpenAI-compatible integrations should use the documented OpenAI-compatible model and chat surfaces.

## Consequences

### Positive

* one authoritative owner of agent conversation state;
* deterministic tool loops;
* safer retries and replay;
* cleaner compatibility with agent runtimes;
* future Playwright support without tying statelessness to browser-page creation;
* warm browser resources can reduce latency without leaking conversation state.

### Costs

* full conversation history may need to be serialized again for each stateless request;
* Playwright requires a separate warm-page lifecycle abstraction;
* Playwright cannot currently provide the same remote-history guarantee as Gemini WebAPI temporary mode;
* stateless model capability must be tracked independently from the general model catalog.

## Non-Goals

This ADR does not:

* change `/v1/chat/completions`;
* remove `/v1/temporary/chat/completions`;
* change existing `conversation_id` semantics;
* make `PersistentTab` stateless;
* require Playwright support in the first implementation;
* guarantee provider-side history deletion for browser-native backends;
* require native Ollama, LM Studio, llama.cpp, or vLLM protocol emulation.

## Implementation Order

1. Add the generic stateless API contract with Gemini WebAPI support.
2. Validate complete agent/tool-loop compatibility.
3. Add a dedicated Playwright warm-page abstraction.
4. Add correctness-first fresh-chat reset.
5. Add Playwright models to stateless discovery.
6. Optimize fresh-chat reset only after reliability is proven.
