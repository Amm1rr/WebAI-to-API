# Provider Contract

This document specifies the interface and responsibilities for logical provider implementations and their execution adapters.

## 1. Ownership Boundaries

- **The Engine owns**: The browser process, context lifecycle, and global synchronization.
- **The Provider (Logical Identity) owns**: Vendor-specific logic, prompt transformation, tool-call parsing, and orchestration of execution adapters.
- **The Adapter (Execution Strategy) owns**: The technical implementation of a specific backend (e.g., Playwright or WebAPI).
- **The Browser-Native Adapter owns**: Request-scoped browser logic, page-level event listeners, observer injection, and prompt emulation.

### 1.1 Authentication Ownership Boundaries

Authentication ownership is split by responsibility:

| Responsibility | Owner | Notes |
| :--- | :--- | :--- |
| Discovery | `AuthLoader` | Finds available auth material such as `[Gemini]` cookies, legacy `[Cookies]`, `runtime/auth/gemini.json`, or browser state. Discovery does not decide which source wins. |
| Selection | Provider-specific selector | Gemini source ordering and fallback sequencing are owned by `GeminiAuthSelector`. |
| Validation | Backend implementation | WebAPI validates cookies through account status evaluation. Playwright validates browser/storage usability through browser-context activation. |
| Activation | Backend implementation | WebAPI activates a direct Gemini client. Playwright activates storage state in a browser context. |
| Caching | `AuthManager` | Owns cached auth status exposed by `/v1/auth/status`. |
| Login and recovery orchestration | `AuthManager` plus provider auth strategy | `AuthManager` coordinates login/status flow; provider strategies perform provider-specific login and post-login recovery hooks. |

`AuthLoader` and `GeminiAuthSelector` must not validate account status, create backend clients, activate browser contexts, or decide WebAPI guest-mode fallback. Backend implementations consume selected candidates and decide whether they are usable for that backend.

For Gemini, source selection order is:

```text
[Gemini] canonical cookies
        ↓
legacy [Cookies] cookies
        ↓
runtime/auth/gemini.json
```

Legacy `[Cookies]` configuration remains supported for backward compatibility. `GeminiAuthStateLoader.load_auth_state_with_fallback()` is retained only as a deprecated compatibility path and is no longer part of the primary runtime selection flow.

### Forbidden Behaviors:
- Providers/Adapters must NEVER call `browser.close()` or `context.close()` directly.
- Providers/Adapters must NEVER recreate `BrowserContext` or sessions directly; they must escalate to the authoritative session layer.
- Browser-native Adapters must NEVER bypass `ProviderSession.acquire_lease()` to obtain pages.
- Providers/Adapters must NEVER manipulate `is_shutting_down` or other engine state flags.

## 2. Page Poisoning Contract

A page is considered **Poisoned** if its integrity is compromised such that future request reliability cannot be guaranteed.

### 2.1 Criteria for Poisoning:
- `page.on("crash")` fires.
- The page closes unexpectedly during an active request.
- Bridge integrity is lost (e.g., failed to expose binding or callback registry corruption).
- Stream ordering becomes corrupted (e.g., queue overflow or out-of-order chunks).

### 2.2 Handling Poisoned Pages:
- **Irreversibility**: Poisoned state is terminal. A poisoned page/tab can NEVER transition back to a healthy or IDLE state.
- **No Reuse**: Poisoned pages must NEVER be returned to the idle pool or reused for future requests.
- **Immediate Invalidation**: Providers MUST mark the associated `PersistentTab` as `DEAD` immediately upon detection.

## 3. Async Task & Governance

Providers must maintain strict ownership boundaries for their asynchronous execution context.

### 3.1 Task Boundaries
- **Request-Scoped Tasks**: Providers may only create and own tasks that belong to a single request lifecycle (e.g., stream observers).
- **Session-Scoped Tasks**: Background loops (reaper, autosave, eviction) are owned exclusively by `ProviderSession`. Providers must NEVER spawn session-wide background tasks.
- **No Orphans**: Providers must not create untracked background tasks. Any spawned task must be cancellable and awaited/cleaned up during teardown.

## 4. Listener & Observer Ownership

Providers are responsible for the entire lifecycle of any side-effects they introduce to a browser page.
- **Request Scoping**: Every listener registered by a provider (`on("close")`, `on("crash")`, bridge callbacks, DOM observers) is fully owned by that specific request lifecycle.
- **Mandatory Detachment**: All listeners and injected observers MUST be detached, removed, or invalidated during the `_cleanup` phase.
- **Contamination Risk**: Leaking listeners across requests is considered a critical cross-request contamination risk and a memory leak.

## 5. Deterministic Teardown Ordering

To prevent late-event races and cross-request contamination, request teardown MUST follow this exact sequence. This order ensures producers are halted before their communication channels are removed:

1. **Stop Observers**: Detach DOM observers and stop browser-side scripts.
2. **Cancel Tasks**: Terminate all request-scoped async tasks (observers, generators) to halt internal producers.
3. **Remove Callbacks**: Pop the `request_id` entry from the page's bridge callback registry.
4. **Flush Queues**: Invalidate or close request-local queues.
5. **Release Lease**: Call `ManagedPage.close()` (shielded) to return the permit and release the tab.

## 6. Failure Escalation Semantics

Providers are responsible for **identifying** and **escalating** failures. The actual execution of recovery or shutdown remains the authoritative responsibility of `ProviderSession` and `BrowserEngine`.

| Failure Scope | Escalation Action | Authority | Examples |
| :--- | :--- | :--- | :--- |
| **Request Only** | Terminate stream / Return Error | Provider | Timeout, client disconnect, selector fail. |
| **Tab/Page** | Invalidate Tab (`DEAD`) | Provider | Renderer crash, bridge loss, corrupted ordering. |
| **Session** | Escalate to session-level recovery | Session | Auth loss, keepalive page closed. |
| **Engine** | Escalate terminal engine failure | Engine | Manual window closure, browser disconnect. |

## 7. Required Lifecycle Hooks

Providers must implement the following logic in their `chat_completions` implementation:

### 7.1 Initialization
- Call `session.acquire_lease()` and verify success.
- Register lifecycle listeners on the returned page.
- Call `session._setup_page_bridge(page)` to ensure bridge availability.

### 7.2 Observer Injection
- Inject the stream extraction script via `page.evaluate`.
- Wait for the JS `ready` signal before submitting prompts.

### 7.3 Hardened Submission
- Acquire `session.submit_lock` before submitting a prompt.
- Use the adapter's `submit_prompt` method.
- Verify submission success via bridge signals before proceeding to stream generation.

### 7.4 Mandatory Cleanup
- **Async Shield Rationale**: All teardown logic MUST be wrapped in `asyncio.shield`. This prevents task cancellation from interrupting the cleanup sequence, which would otherwise cause critical resource leaks (stale locks, semaphore permits, or callback registry entries).
- **Idempotency Invariant**: Cleanup paths and `ManagedPage.close()` MUST be idempotent. Teardown logic must be safe for multiple concurrent or sequential executions during task cancellation races.
- **Best-Effort Execution**: Request cleanup is a best-effort operation. Failures in individual cleanup steps (e.g., failed listener removal or observer shutdown) MUST NOT block or terminate the remaining sequence. Critical resource release (permits/locks) must proceed regardless of auxiliary step outcomes.
- **Cancellation Invariant**: Request cancellation is a first-class lifecycle path. Cleanup guarantees must remain identical regardless of whether the request succeeded, failed, timed out, or was cancelled by the client.
- **Deterministic Order**: Follow the **Deterministic Teardown Ordering** sequence strictly.
- **Task Integrity**: Ensure all provider-spawned async tasks are terminated.

## 8. Retry & Recovery Boundary

### 8.1 Internal Retries (Permitted):
Providers may implement internal retry logic for transient UI instability:
- Transient selector failures or element visibility races.
- Temporary DOM instability.
- Navigation races during initial page load.

### 8.2 Terminal Failures (Retry Forbidden):
Providers must NOT retry the following conditions; they must fail the request:
- **Poisoned Pages**: Attempting to reuse a crashed or corrupted page.
- **Corrupted Streams**: Detecting out-of-order data or queue saturation.
- **Shutdown State**: Attempting operations after `is_shutting_down` is set.
- **Auth Loss**: Detecting authentication-invalid contexts (must escalate to trigger session-level recovery).

### 8.3 Lock Hierarchy Compliance
- Providers MUST adhere to the global lock hierarchy (Management -> Init -> Registry -> Tab).
- Providers MUST NOT hold locks during long Playwright operations or network waits.

## 9. Streaming Responsibilities

- **SSE Normalization**: Providers must use `format_sse_chunk` and `get_done_chunk` utilities.
- **Heartbeat Propagation**: Providers must call `tab.heartbeat()` during long-running tasks to prevent orphan cleanup.
- **Timeout Handling**: Implement `chunk_timeout` and `total_request_timeout`.

## 10. AI Agent Rules

AI Agents working on provider implementations must adhere to these strict constraints:

1. **Never Reuse Poisoned Pages**: Invalidation is terminal and irreversible.
2. **Never Leak Listeners**: Ensure strict request-scoped detachment.
3. **No Orphan Tasks**: Providers may only own request-scoped tasks; never bypass session authority.
4. **No Recovery Loops**: Never introduce provider-level recovery loops that bypass the engine/session lifecycle authority.
5. **Deterministic Cleanup**: Always follow the documented teardown ordering.

## 11. Gemini API Conversation Contract

This section provides a summary of the specific fields added to the chat completions API to support stateful Gemini conversations. For authoritative definitions and persistence guarantees, see **[API Contract](api-contract.md)**.

Provider implementations define their own recovery mechanism and must document their `conversation_id` and `reused_conversation` semantics clearly. For example, Gemini WebAPI uses SQLite-backed `ChatSession` snapshots, while Gemini Playwright uses provider-side Gemini conversation URLs plus in-memory `PersistentTab` reuse.

### 11.1 Input / Output Fields

#### conversation_id
* **Type**: `str` (Opaque, URL-safe token)
* **Presence**: Optional request field.
* **Behavior**: 
  * If omitted in the request, the system automatically generates a new cryptographically secure `conversation_id` and bootstraps a fresh session.
  * If provided, the selected provider/backend attempts continuation using its documented recovery mechanism.
* **Response**: Always returned in the top-level response payload to allow the client to persist the thread context.

#### reused_conversation
* **Type**: `bool`
* **Presence**: Always present in the response payload.
* **Behavior**: 
  * For Gemini WebAPI, returns `true` when an existing or restored `ChatSession` was reused.
  * For Gemini Playwright, returns `true` when an existing in-memory `PersistentTab` was reused. URL-backed provider-side recovery after a restart may still report `false` because no in-memory tab was reused.
  * Stateless providers may omit the field or define backend-specific semantics.
