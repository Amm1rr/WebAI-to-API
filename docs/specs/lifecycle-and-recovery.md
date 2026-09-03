# Lifecycle and Recovery

This document specifies the browser lifecycle, state transitions, and self-healing protocols.

## 1. Startup & Generations

### 1.1 Generation Rollover & Invalidation
- **Generation ID**: The engine maintains a `browser_generation` counter.
- **Trigger**: Every successful new Browser process launch increments this counter. A failed launch leaves it unchanged.
- **Initialization State**: A newly created session starts with `last_browser_generation = None` and is not associated with any browser generation until its first context initialization.
- **Propagation**: Sessions track `last_browser_generation`. A generation rollover is detected only after the session has been initialized (`last_browser_generation is not None`) and the tracked generation differs from the engine generation. On rollover detection, the session MUST purge all registry tabs immediately.
- **Invalidation Invariant**: A generation rollover permanently invalidates all existing `PersistentTab` leases. Old tabs/pages from previous generations are stale and MUST NEVER be reused.


### 1.2 Recovery Authority Boundaries
- **Provider/Adapter Role**: Providers and their adapters may only **identify** and **escalate** recovery requests.
- **Session Authority**: `ProviderSession` owns authoritative session-scoped recovery logic (context recreation, tab invalidation, and recovery coordination).
- **Engine Authority**: `BrowserEngine` is the authoritative lifecycle orchestrator and terminal shutdown authority. It manages process-level state transitions but does NOT act as a recreation mechanism after shutdown has been initiated.

### 1.3 Recovery Concurrency & Convergence
- **Convergence**: Concurrent `ensure_healthy()` calls must converge into a single recovery path.
- **Serialization**: Redundant context recreation races are suppressed via `ProviderSession.init_lock`. Only the first caller performs the setup; subsequent concurrent callers wait and verify the new state.

### 1.4 Browser Replacement Order
Unhealthy-browser replacement runs under `BrowserEngine.management_lock`:

1. Clean ProviderSessions bound to old lifecycle/resources and wait for active cleanup.
2. Close or skip old Browser through the runtime (`runtime.close_browser()`).
3. Stop the driver through the runtime (`runtime.stop()`).
4. Start the driver and launch a new Browser through the runtime (`runtime.start()`, `runtime.launch_browser()`).
5. Publish the successful launch by incrementing `browser_generation`.
6. Set up ProviderSession contexts.

ProviderSessions observe/store `last_browser_generation`; they do not choose generations. A close callback from generation N cannot terminate generation N+1.

## 2. Window Liveness & Terminal Shutdown

### 2.1 The "Zombie" Chromium Problem
- **Behavior**: Manual window closure may not immediately trigger a `disconnected` event if Chromium remains active in the background.
- **Canonical Source**: The status of the `keepalive_page` is the authoritative source for window liveness.
- **Hardening**: `ProviderSession.is_alive` MUST check `keepalive_page.is_closed()`.

### 2.2 Deterministic Shutdown Ordering
Application shutdown follows:

1. `ApplicationServer.handle_exit()` marks generic `app.shutdown` intent and `BrowserEngine.shutdown_requested(source="application")` (first source wins, thread-safe).
2. Uvicorn stops accepting new connections and drains active connections/tasks (`timeout_graceful_shutdown=15`), while new page/session admission is rejected.
3. Active requests receive up to 15 seconds to complete.
4. Remaining ASGI tasks are timeout-cancelled with `Task cancelled, timeout graceful shutdown exceeded`; the outermost shutdown-aware ASGI boundary completes them as expected termination: pre-header → `503 Service Unavailable` (`Connection: close`), post-header streaming → truncated without `[DONE]`.
5. FastAPI lifespan shutdown runs (`app.main:lifespan`).
6. ProviderSession lifecycle tasks are cancelled/drained, Gemini client and `BrowserEngine` resources close (session/browser/driver via `BrowserRuntime`).
7. Process exits.

Emergency operator force-quit: a second `SIGINT` while shutdown is already active logs one concise warning and terminates immediately via `os._exit(130)` without awaiting cleanup. This applies only to repeated `SIGINT`; `SIGTERM` and programmatic `request_shutdown()` retain normal graceful `15s` drain, while `SIGINT` after programmatic shutdown is treated as explicit force-quit.

**Startup Shutdown Invariant**: Shutdown intent received during application startup (e.g., `SIGINT`/`Ctrl+C` during FastAPI lifespan startup while Gemini initialization is in progress) prevents transition into normal serving state and proceeds through application lifespan cleanup once startup reaches a safe completion point. The server does not log `Uvicorn running on ...`, does not schedule update-check, and does not require immediate cancellation of the in-progress Gemini initialization; the current initialization step may finish before lifespan shutdown executes.

ProviderSession resources always close before Browser, and Browser before the driver stops. `BrowserEngine` owns this ordering and all shutdown/recovery decisions; the runtime only executes the mechanics. Runtime terminal cleanup uses `save_state=False`; bootstrap/manual auth cleanup may use `save_state=True`.

**Shutdown Invariant**: Once shutdown intent exists, no new recovery or admission may begin. Workers recheck shutdown state after acquiring `init_lock` and before cleanup. Existing recovery may be cancelled and awaited during terminal cleanup.

### 2.3 Context Close Classification
- **Intentional close**: ProviderSession cleanup owns the context close and marks it so the callback cannot trigger terminal shutdown.
- **Stale callback**: A callback for an old context or generation is ignored.
- **Unexpected current-context close**: Without shutdown intent or an intentional marker, this triggers browser-disconnect terminal behavior.
- **Application shutdown**: Current-context closure is expected activity and does not restart crash recovery or shutdown orchestration. Active requests still receive immediate terminal browser-disconnect signals when their browser resource disappears.

### 2.4 Active Request Termination
Browser/page/context loss signals the request terminal event immediately. Before SSE headers are sent, `BrowserRequestExecutor` maps terminal `BrowserDisconnectedError` through the existing HTTP 502 contract. After headers are sent, the stream boundary consumes the terminal state without waiting for queue, chunk, total, or request timeout. If a raw Playwright close error arrives later for the same closure, the recorded terminal browser-disconnect state takes precedence; raw Playwright errors without that state retain existing behavior.

Request cleanup owns lease and semaphore release. BrowserEngine never decrements lease counters directly.

During application shutdown, an active stream may already have sent headers when browser or shutdown termination occurs. The stream boundary consumes that terminal state, ends without `[DONE]`, and still completes request cleanup and lease release.

## 3. Recovery Boundaries

The system classifies failures into two categories with distinct response protocols.

### 3.1 Recoverable Failures (Triggers Self-Healing)
- **Scope**: Transient transport or UI errors where the **browser process remains authoritative and alive**.
- **Boundary**: Recoverable flows may recreate session/context state **ONLY while the engine remains healthy**.
- **Protocol**: Call `ensure_healthy()` -> context refresh or tab purge. Self-healing MUST NOT recreate the browser process.
- **Examples**:
    - Request timeout.
    - Navigation failure.
    - DOM element missing.
    - Authentication expiry.

### 3.2 Terminal Failures (Triggers Engine Shutdown)
- **Scope**: Fatal loss of the user-visible interface or global process. The **recovery boundary has been crossed** and the system cannot restore structural integrity.
- **Boundary**: No context or browser recreation is permitted after terminal shutdown begins. Shutdown is irreversible (Terminal Shutdown Immutability).
- **Protocol**: Call `BrowserEngine.close()`. No recreation allowed.
- **Examples**:
    - Manual window closure.
    - Explicit browser disconnect.
    - System-level shutdown signal (`SIGINT`).

An application shutdown signal establishes shutdown intent before connection drain; it does not start a second crash-classification path.

### 3.3 Recovery Failure Escalation
If recovery itself fails, the failure MUST be escalated:
- **Session Loss**: Failed context recreation escalates to session invalidation or terminal engine shutdown.
- **No Loops**: Repeated recovery loops for the same fatal condition are forbidden. If `ensure_healthy()` cannot restore invariants, the system must fail terminal.

## 4. ensure_healthy Contract

The `ensure_healthy()` method acts as both a system validator and a recovery escalation entrypoint.

- **Fail Fast**: It MUST raise `RuntimeError` immediately if called during terminal shutdown.
- **Strict Invariants**: It MUST NOT silently succeed if liveness invariants (e.g., connected browser, open keepalive page) are violated.
- **Authority Escalation**: It may escalate recovery to authoritative lifecycle managers (`ProviderSession` for context recreation or `BrowserEngine` for browser lifecycle handling).
- **Process Protection**: Provider code MUST NEVER directly invoke browser-process recreation; it must escalate through the engine's authoritative path.
- **No Resurrection Invariant**: `ensure_healthy()` may coordinate recovery only within the current healthy engine lifecycle. It MUST NEVER attempt to resurrect or re-initialize a terminated engine generation.

## 5. Operational Invariants

- **State Immutability**: Once `is_shutting_down` is `True`, it can NEVER return to `False`.
- **Graceful Draining**: The shutdown sequence allows a 15-second grace period for active requests to finish before killing resources.
- **Safe Teardown**: Task scheduling in disconnect callbacks MUST use the defensive `try/except RuntimeError` pattern to avoid loop-teardown races.

## 6. AI Agent Rules

AI Agents working on lifecycle or recovery logic must adhere to these strict constraints:

1. **No Resurrection**: Never attempt to re-initialize or "self-heal" a browser or context after `is_shutting_down` is set.
2. **Strict Generation Invalidation**: Always invalidate all registry tabs on generation mismatch; never attempt "best-effort" reuse of stale pages.
3. **Respect Authority Boundaries**: Never bypass the `BrowserEngine` or `ProviderSession` for process/context creation.
4. **Converge Recoveries**: Ensure all recovery logic is guarded by locks and converges into a single path; never allow parallel context recreation.
5. **Fail terminal**: If `ensure_healthy` cannot restore structural invariants, escalate to terminal shutdown rather than entering a recovery loop.

## 7. Conversation Continuity Models

Conversation recovery is provider/backend-dependent. Browser lifecycle recovery remains owned by `BrowserEngine` and `ProviderSession`; conversation continuity for a chat request is owned by the selected provider/backend.

### 7.1 Gemini WebAPI Generation Authority

Gemini lifecycle generation IDs are chosen only by `init_gemini_client()`.

- The initial generation ID is selected by lifecycle initialization.
- Replacement generation IDs are selected by lifecycle initialization.
- `_register_generation(client, generation)` registers an explicit ID; it never chooses one.
- Request, session, registry, restore, and streaming paths MUST NOT allocate, increment, or repair generation IDs.

Candidate replacement follows this commit order:

1. Initialize candidate client.
2. Select and register candidate generation.
3. Update the session registry with the explicit generation.
4. Publish global client and current generation.
5. Retire the old generation.

If candidate initialization or registry commit fails, the candidate record is removed, the candidate is closed, and the old lifecycle state remains current.

Current client, current generation, generation record, and reverse client mapping MUST remain coherent. Malformed state raises a lifecycle invariant `RuntimeError`; no implicit generation repair exists.

Lifecycle error distinctions are:

- `GeminiClientNotInitializedError`: initialization is unavailable.
- `GeminiGenerationUnavailableError`: an expected stale or retired generation race.
- `RuntimeError`: malformed lifecycle state or shutdown rejection of new lease acquisition.

### 7.2 Gemini WebAPI SessionRegistry

The Gemini WebAPI backend uses `SessionRegistry` as an in-memory container protected by asyncio synchronization primitives. It maps cryptographically secure opaque tokens (`conversation_id`) to `SessionManager` instances and coordinates SQLite-backed snapshot recovery through the conversation repository.

The registry constructor requires a registered `(client, generation)` pair. `update_client()` and `reopen()` also require a registered generation. The registry MUST validate and propagate the supplied generation, but MUST NOT allocate, increment, or repair generation IDs. Every `SessionManager` receives the registry's exact `client_generation`; its separate `session_generation` tracks the generation of its current `ChatSession`.

### 7.3 Gemini WebAPI conversation_id Lifecycle
1. **Creation**: When a completions request is submitted without a `conversation_id`, the system generates a secure opaque token.
2. **Registry Mapping**: The token maps to a dedicated `SessionManager` in memory, which lazily instantiates a `ChatSession`.
3. **API Return**: The `conversation_id` token is returned as a top-level key in the standard OpenAI-compatible completions response body.
4. **Client Preservation**: Subsequent requests by the same client present this token in the request body to continue the thread.

### 7.4 Gemini WebAPI SessionManager Reuse Behavior
If the registry finds an active `SessionManager` for the given token, it reuses the session if and only if the requested model and gem match. The manager acts as a long-lived state container, bypassing the initialization overhead of the client and retaining history natively.

### 7.5 Gemini WebAPI Bootstrap and Persistent Recovery
* **Bootstrapping**: On the first request of a new session, the provider concatenates the entire conversation history from `messages` to bootstrap the thread on Google's backend.
* **Persistent Recovery**: The system utilizes a SQLite-backed repository to persist session snapshots. If a session is lost from memory (e.g., pruned due to TTL or after a server restart), the `SessionRegistry` can automatically restore the session state from the database using the supplied `conversation_id`.
* **Durable Continuity**: For Gemini WebAPI, long-running threads can remain continuous across process restarts or container recycling, provided the `conversation_id` is preserved by the client and the corresponding SQLite snapshot exists.
* **Missing Snapshot Behavior**: If an existing `conversation_id` is not present in memory and no valid snapshot exists, the request fails explicitly. The current implementation does not silently rebuild an existing WebAPI conversation from incoming message history.
* **Deletion**: Gemini WebAPI deletion reserves the local `conversation_id` in `SessionRegistry` before remote deletion begins. This tombstone blocks concurrent reuse or SQLite restoration while the remote Gemini chat is being deleted and local state is being removed.

### 7.6 Gemini WebAPI Restore Coordination and Generation Ownership
Each restore captures client generation `N` and acquires a lease for that exact generation before snapshot recovery, validation, deserialization, and `ChatSession` construction. A replacement may retire `N`, but cannot close it while the restore lease remains active.

Before publication, the restore rechecks registry shutdown state, the deletion tombstone, existing session, and current client/generation. A changed generation discards the candidate, releases lease `N`, and retries with the current generation. A concurrently created session may win; restore never overwrites it. Failed or cancelled restores clear in-flight coordination state. Restore preserves its existing pruning and capacity semantics.

Registry shutdown closes restore admission, cancels and awaits pending restore producers, and releases their leases before Gemini client shutdown. Successful lifecycle initialization explicitly reopens the existing registry without clearing in-memory sessions; stale chat sessions rebuild against the current explicit generation on next use.

### 7.7 Gemini WebAPI Client Generation Retirement
Each committed Gemini WebAPI client has a generation record. Request paths lease the generation for the duration of direct client use. Replacement marks the old generation retired; the old client closes immediately only when no leases remain, otherwise its final lease release closes it. Shutdown rejects new leases, closes zero-lease records, and preserves active-leased records until release.

Shutdown never creates generation records. It retires all registered records, closes zero-lease records, and leaves active-leased generations alive until their final release. If the current client is malformed and is not represented by a record, shutdown defensively closes that client directly; it never creates a synthetic generation for cleanup.

### 7.8 Gemini Playwright URL-Backed Continuity

The Gemini Playwright backend does not use SQLite conversation snapshots. It uses two continuity mechanisms:

* **Live Tab Reuse**: `ProviderSession.conversation_registry` maps Gemini provider conversation IDs to in-memory `PersistentTab` instances. If a matching tab is live and valid, the request reuses that tab.
* **Provider-Side URL Recovery**: If no live tab is available but the request supplies a `conversation_id`, the backend navigates a browser page to `https://gemini.google.com/app/{conversation_id}` and relies on Gemini's provider-side conversation history.

For new Playwright conversations, the provider-side `conversation_id` is discovered from the Gemini URL after submission and the temporary page is promoted to a `PersistentTab` in `ProviderSession.conversation_registry`.

`reused_conversation=true` in Playwright indicates live in-memory `PersistentTab` reuse. After a process restart or context recreation, Playwright may still resume the provider-side Gemini thread by URL navigation while reporting `reused_conversation=false` because no in-memory tab was reused.

### 7.9 Model and Gem Switching
If a stateful request switches models (e.g. from `gemini-3-flash` to `gemini-3-pro`) or changes the gem ID:
* **Gemini WebAPI**: `SessionManager._ensure_session()` detects the mismatch, replaces the `ChatSession`, and uses full prompt concatenation on the current request to bootstrap the new model/gem context.
* **Gemini Playwright**: model/gem behavior is handled by the Playwright adapter and provider UI flow, not by SQLite snapshots.

### 7.10 Session Pruning Policy
To protect server memory, the `SessionRegistry` passively prunes idle sessions when the cache capacity exceeds `MAX_SESSIONS = 500`.
* **Prunability Invariant**: A session can ONLY be pruned if it is unlocked (`manager.lock` is not locked) and has `active_streams == 0` (no active progressive stream tasks).
* **TTL Policy**: Stale sessions are evicted if their idle time exceeds `IDLE_TIMEOUT = 3600` (60 minutes).
