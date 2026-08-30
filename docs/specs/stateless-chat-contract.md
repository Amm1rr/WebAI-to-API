# Stateless Chat Execution Contract

> **Status:** Accepted Design — Phase 1 Gemini WebAPI Implemented; Playwright Pending
> **Scope:** `/v1/stateless/*`
> **Authority:** This specification defines the stateless execution contract. The current implementation supports Gemini WebAPI only; Playwright support remains planned and MUST NOT be represented as available until implemented and tested.

## 1. Purpose

The stateless chat API is designed for clients that own their conversation history, including autonomous agents and other OpenAI-compatible runtimes.

The core invariant is:

> Every request contains all conversational state required to execute that request.

No previous stateless request may be required for semantic continuation.

## 2. API Surface

```text
GET  /v1/stateless/models
POST /v1/stateless/chat/completions
```

The current Phase 1 implementation supports this surface for direct Gemini WebAPI execution only.

The request and response formats SHOULD remain compatible with the project's OpenAI Chat Completions surface unless this contract explicitly defines otherwise.

## 3. State Ownership

### Client-owned state

The client owns:

* conversation history;
* system/developer instructions;
* assistant messages;
* tool-call history;
* tool results;
* context compression;
* retry/replay decisions.

### Server-owned execution state

WebAI-to-API owns only resources necessary to execute the current request, such as:

* provider client leases;
* browser page leases;
* request observers;
* stream queues;
* request-scoped temporary files;
* request lifecycle metadata.

Execution-resource reuse MUST NOT imply conversation-state reuse.

## 4. `conversation_id`

`conversation_id` is incompatible with the stateless continuation model.

The stateless chat endpoint MUST reject a supplied `conversation_id`.

It MUST NOT:

* create a public continuation token;
* require a continuation token on subsequent turns;
* restore a previous stateless request;
* associate one stateless request with another through hidden conversation state.

## 5. Message History

The backend MUST process the complete supplied message list needed for the current turn.

Supported conversational roles must preserve OpenAI-compatible semantics required by the selected backend, including at minimum:

* `system`;
* `user`;
* `assistant`;
* `tool`.

Assistant tool calls and corresponding tool results MUST remain reconstructable from the supplied request history.

Backend-specific prompt transformation may flatten these messages, but it MUST NOT intentionally depend on a previous stateless request for missing history.

## 6. Tool Calling

Tools are request-scoped.

A stateless request may supply OpenAI-compatible `tools`.

When a model requests a tool, the response SHOULD use the existing OpenAI-compatible structured tool-call shape.

The normal agent loop is:

```text
Request N
  messages + tools
       |
       v
assistant.tool_calls
       |
       v
client executes tool
       |
       v
Request N+1
  previous messages
  + assistant tool call
  + tool result
  + tools
```

Request N+1 MUST be executable without access to the backend conversation used for Request N.

`tool_choice` and other optional OpenAI request controls are separate capability concerns and MUST NOT be claimed as supported unless their semantics are implemented and tested.

## 7. Gemini WebAPI Backend

Gemini WebAPI is the first intended implementation.

It MUST:

* use `temporary=True`;
* reject `conversation_id`;
* avoid `SessionRegistry` conversation continuation;
* avoid SQLite conversation snapshots;
* avoid Gemini history persistence provided by normal non-temporary requests;
* transform the full supplied message history;
* support tools through the shared tool-prompt and tool-call normalization path;
* support streaming and buffered responses according to the existing OpenAI-compatible response contract.

## 8. Gemini Playwright Backend

Playwright support is planned but is not required for the initial stateless implementation.

When implemented, it MUST preserve stateless conversation semantics despite browser-resource reuse.

### 8.1 Conversation Isolation

Every Playwright stateless request MUST execute from a verified fresh Gemini chat state.

It MUST NOT:

* continue the prior stateless request's Gemini thread;
* reuse a provider-side conversation because the browser page was reused;
* register the request as a stateful `PersistentTab` conversation;
* expose a generated Gemini conversation ID as stateless continuation state.

### 8.2 Warm Page Reuse

Playwright MAY reuse browser pages to avoid the cost of:

* `context.new_page()`;
* initial page load;
* repeated SPA startup;
* repeated authentication bootstrap.

A reusable stateless page is an execution resource, not a conversation resource.

The future runtime SHOULD maintain a dedicated warm-page pool independent from `conversation_registry`.

Conceptually:

```text
ProviderSession
├── Persistent Conversation Registry
│   └── PersistentTab
│       └── stateful /v1/chat/completions
│
└── Stateless Warm Page Pool
    └── WarmPage
        └── stateless /v1/stateless/chat/completions
```

### 8.3 Warm Page Lifecycle

A stateless warm page MUST have an exclusive request lease.

Before execution it MUST:

1. belong to the current browser generation;
2. be structurally alive;
3. be in a known reusable state;
4. enter a fresh Gemini chat;
5. verify that the input UI is ready.

After execution it MUST:

1. stop request observers;
2. remove request bridge callbacks;
3. remove request-specific listeners;
4. release request ownership;
5. ensure generation consistency;
6. either return cleanly to the pool or be invalidated.

Any uncertain cleanup or state corruption MUST poison the page rather than return it to the pool.

### 8.4 Reset Strategy

The baseline reset mechanism SHOULD prioritize correctness.

Initial implementation SHOULD use deterministic navigation/reset to the canonical Gemini new-chat surface.

A direct UI `New Chat` action MAY later be used as a performance optimization if independently verified to:

* clear conversational state;
* preserve authentication;
* leave no previous conversation context;
* reach a stable request-ready state.

The optimized path MUST retain a fallback to the correctness-first reset path.

### 8.5 Provider-Side History

Playwright stateless execution does not guarantee absence of Gemini-side history.

The contract guarantees only that WebAI-to-API will not reuse that conversation as hidden state for subsequent stateless requests.

This distinction MUST be documented publicly if Playwright stateless support is exposed.

## 9. Model Discovery

`GET /v1/stateless/models` returns only models that support the stateless execution contract.

It MUST NOT advertise a model merely because it appears in `/v1/models`.

Initial expected scope:

```text
Gemini WebAPI models
```

Future scope may include:

```text
playwright/gemini/...
```

only after the Playwright stateless lifecycle is implemented and tested.

Model IDs remain provider/backend-aware and MUST follow the project's canonical routing rules.

## 10. Streaming

The stateless endpoint supports OpenAI-compatible SSE streaming.

A successful stream normally terminates with:

```text
data: [DONE]
```

Backends MAY implement tool-call streaming through buffered compatibility replay when native progressive tool parsing is unavailable.

The client-facing structured response must remain compatible with the expected Chat Completions tool-call format.

## 11. Persistence

Stateless requests MUST NOT create WebAI-owned durable conversation continuation state.

| Backend           | SQLite Conversation Snapshot | WebAI Conversation Reuse | Provider History        |
| ----------------- | ---------------------------: | -----------------------: | ----------------------- |
| Gemini WebAPI     |                           No |                       No | No via `temporary=True` |
| Gemini Playwright |                           No |                       No | May exist               |
| Future backend    |                           No |                       No | Backend-dependent       |

Request-scoped operational metadata, logs, metrics, or temporary files are not considered conversation continuation state.

## 12. Relationship to Existing APIs

### `/v1/chat/completions`

Remains the primary provider-aware API where conversation continuity may be backend-dependent and `conversation_id` may be supported.

### `/v1/temporary/chat/completions`

Remains a specialized Gemini WebAPI endpoint with the stronger `temporary=True` persistence guarantee.

It MUST NOT be silently broadened to Playwright if that would weaken its existing no-Gemini-history contract.

### `/v1/stateless/chat/completions`

Provides the generic client-owned-history contract.

The public meaning of `stateless` is consistent across backends even when their internal execution mechanics differ.

## 13. OpenAI Compatibility Parameters

The stateless endpoint uses same backend-aware request-control contract as primary and temporary chat endpoints.
Controls are validated before Gemini lease acquisition or request normalization.

| Control | Gemini WebAPI stateless behavior |
| --- | --- |
| `max_tokens`, `max_completion_tokens` | Accepted for compatibility, no effect |
| `reasoning_effort` | Accepted for compatibility, no effect |
| `stream_options.include_usage` | Accepted for compatibility, no effect |
| `temperature`, `top_p`, `top_k` | HTTP 400 unsupported |
| `response_format`, `parallel_tool_calls` | HTTP 400 unsupported |
| `tool_choice` | HTTP 400 unsupported |

Malformed values and simultaneous `max_tokens` plus `max_completion_tokens` return HTTP 422. Accepted no-effect
controls are not forwarded, do not synthesize usage, and do not alter extended-thinking or persistence behavior.
A field MUST NOT be documented as semantically supported merely because schema validation accepts or ignores it.

## 14. Error Semantics

Errors SHOULD use the same public policy as the corresponding OpenAI-compatible API.

Capability failures must be explicit.

Examples include:

* model not available for stateless execution;
* backend not supporting stateless execution;
* unsupported content parts;
* unsupported provider options;
* supplied `conversation_id`.

The server MUST NOT silently fall back from stateless execution to stateful conversation continuation.

## 15. Browser Runtime Boundaries

Future Playwright support must preserve existing BrowserEngine and ProviderSession lifecycle authority.

The stateless warm-page design MUST:

* remain generation-aware;
* use managed leases;
* obey request concurrency bounds;
* participate in deterministic shutdown;
* release resources under cancellation;
* never bypass BrowserEngine/ProviderSession ownership;
* never reuse poisoned pages.

Warm-page pooling is an additional ProviderSession-owned resource class, not a replacement for `PersistentTab`.

## 16. Compatibility Non-Goals

The stateless surface does not require emulation of native local-server protocols.

The project MUST NOT add endpoints such as Ollama, LM Studio, llama.cpp, or vLLM-specific discovery probes solely to suppress client detection errors.

Protocol compatibility should be implemented only when intentionally supported as a real compatibility surface.

## 17. Rollout Contract

Implementation proceeds in stages.

### Stage 1 — Gemini WebAPI (Implemented)

* expose `/v1/stateless/models`;
* expose `/v1/stateless/chat/completions`;
* enforce client-owned history;
* reject `conversation_id`;
* support normal chat;
* support streaming;
* support tool calling;
* verify multi-turn tool loops;
* verify no SQLite snapshot creation;
* verify temporary Gemini execution.

### Stage 2 — Playwright Foundation

* introduce stateless warm-page ownership;
* separate warm pages from `PersistentTab`;
* implement deterministic fresh-chat reset;
* implement full-history serialization;
* support tool-call normalization;
* verify cleanup and generation safety.

### Stage 3 — Playwright Optimization

* benchmark navigation-based reset;
* audit Gemini's direct `New Chat` UI action;
* introduce faster reset only if semantics are equivalent;
* retain a safe fallback.

## 18. Fundamental Invariants

The following invariants are authoritative:

1. **Client owns stateless conversation history.**
2. **A stateless request must be independently executable from its supplied input.**
3. **Browser-resource reuse must never imply conversation reuse.**
4. **Stateful `PersistentTab` and stateless warm pages are separate resource concepts.**
5. **Correctness takes precedence over warm-page reuse.**
6. **A poisoned or uncertain page is invalidated, not recycled.**
7. **Playwright may differ from WebAPI in provider-side history, but not in WebAI-owned conversation continuity.**
8. **Existing stateful and temporary endpoint guarantees remain unchanged.**
