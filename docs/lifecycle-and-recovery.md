# Lifecycle and Recovery

This document specifies the browser lifecycle, state transitions, and self-healing protocols.

## 1. Startup & Generations

### 1.1 Generation Rollover & Invalidation
- **Generation ID**: The engine maintains a `browser_generation` counter.
- **Trigger**: Every new browser process launch increments this counter.
- **Initialization State**: A newly created session starts with `last_browser_generation = None` and is not associated with any browser generation until its first context initialization.- **Propagation**: Sessions track `last_browser_generation`. A generation rollover is detected only after the session has been initialized (`last_browser_generation is not None`) and the tracked generation differs from the engine generation. On rollover detection, the session MUST purge all registry tabs immediately.
- **Invalidation Invariant**: A generation rollover permanently invalidates all existing `PersistentTab` leases. Old tabs/pages from previous generations are stale and MUST NEVER be reused.


### 1.2 Recovery Authority Boundaries
- **Provider/Adapter Role**: Providers and their adapters may only **identify** and **escalate** recovery requests.
- **Session Authority**: `ProviderSession` owns authoritative session-scoped recovery logic (context recreation, tab invalidation, and recovery coordination).
- **Engine Authority**: `BrowserEngine` is the authoritative lifecycle orchestrator and terminal shutdown authority. It manages process-level state transitions but does NOT act as a recreation mechanism after shutdown has been initiated.

### 1.3 Recovery Concurrency & Convergence
- **Convergence**: Concurrent `ensure_healthy()` calls must converge into a single recovery path.
- **Serialization**: Redundant context recreation races are suppressed via `ProviderSession.init_lock`. Only the first caller performs the setup; subsequent concurrent callers wait and verify the new state.

## 2. Window Liveness & Terminal Shutdown

### 2.1 The "Zombie" Chromium Problem
- **Behavior**: Manual window closure may not immediately trigger a `disconnected` event if Chromium remains active in the background.
- **Canonical Source**: The status of the `keepalive_page` is the authoritative source for window liveness.
- **Hardening**: `ProviderSession.is_alive` MUST check `keepalive_page.is_closed()`.

### 2.2 Deterministic Shutdown Ordering
To ensure resource integrity, terminal shutdown MUST follow this exact sequence:
1. **Halt**: Stop accepting new request work.
2. **Flag**: Set `is_shutting_down = True`.
3. **Drain**: Allow active requests a 15s grace period to complete.
4. **Cancel**: Terminate all background loops (reaper, autosave, eviction).
5. **Persist**: Atomically save context state to disk.
6. **Release**: Physically close contexts and the Playwright browser process.

**Shutdown Invariant**: Once the shutdown sequence begins, all future recovery attempts MUST fail immediately.

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

### 7.1 Gemini WebAPI SessionRegistry

The Gemini WebAPI backend uses `SessionRegistry` as an in-memory container protected by asyncio synchronization primitives. It maps cryptographically secure opaque tokens (`conversation_id`) to `SessionManager` instances and coordinates SQLite-backed snapshot recovery through the conversation repository.

### 7.2 Gemini WebAPI conversation_id Lifecycle
1. **Creation**: When a completions request is submitted without a `conversation_id`, the system generates a secure opaque token.
2. **Registry Mapping**: The token maps to a dedicated `SessionManager` in memory, which lazily instantiates a `ChatSession`.
3. **API Return**: The `conversation_id` token is returned as a top-level key in the standard OpenAI-compatible completions response body.
4. **Client Preservation**: Subsequent requests by the same client present this token in the request body to continue the thread.

### 7.3 Gemini WebAPI SessionManager Reuse Behavior
If the registry finds an active `SessionManager` for the given token, it reuses the session if and only if the requested model and gem match. The manager acts as a long-lived state container, bypassing the initialization overhead of the client and retaining history natively.

### 7.4 Gemini WebAPI Bootstrap and Persistent Recovery
* **Bootstrapping**: On the first request of a new session, the provider concatenates the entire conversation history from `messages` to bootstrap the thread on Google's backend.
* **Persistent Recovery**: The system utilizes a SQLite-backed repository to persist session snapshots. If a session is lost from memory (e.g., pruned due to TTL or after a server restart), the `SessionRegistry` can automatically restore the session state from the database using the supplied `conversation_id`.
* **Durable Continuity**: For Gemini WebAPI, long-running threads can remain continuous across process restarts or container recycling, provided the `conversation_id` is preserved by the client and the corresponding SQLite snapshot exists.
* **Missing Snapshot Behavior**: If an existing `conversation_id` is not present in memory and no valid snapshot exists, the request fails explicitly. The current implementation does not silently rebuild an existing WebAPI conversation from incoming message history.

### 7.5 Gemini Playwright URL-Backed Continuity

The Gemini Playwright backend does not use SQLite conversation snapshots. It uses two continuity mechanisms:

* **Live Tab Reuse**: `ProviderSession.conversation_registry` maps Gemini provider conversation IDs to in-memory `PersistentTab` instances. If a matching tab is live and valid, the request reuses that tab.
* **Provider-Side URL Recovery**: If no live tab is available but the request supplies a `conversation_id`, the backend navigates a browser page to `https://gemini.google.com/app/{conversation_id}` and relies on Gemini's provider-side conversation history.

For new Playwright conversations, the provider-side `conversation_id` is discovered from the Gemini URL after submission and the temporary page is promoted to a `PersistentTab` in `ProviderSession.conversation_registry`.

`reused_conversation=true` in Playwright indicates live in-memory `PersistentTab` reuse. After a process restart or context recreation, Playwright may still resume the provider-side Gemini thread by URL navigation while reporting `reused_conversation=false` because no in-memory tab was reused.

### 7.6 Model and Gem Switching
If a stateful request switches models (e.g. from `gemini-3-flash` to `gemini-3-pro`) or changes the gem ID:
* **Gemini WebAPI**: `SessionManager._ensure_session()` detects the mismatch, replaces the `ChatSession`, and uses full prompt concatenation on the current request to bootstrap the new model/gem context.
* **Gemini Playwright**: model/gem behavior is handled by the Playwright adapter and provider UI flow, not by SQLite snapshots.

### 7.7 Session Pruning Policy
To protect server memory, the `SessionRegistry` passively prunes idle sessions when the cache capacity exceeds `MAX_SESSIONS = 500`.
* **Prunability Invariant**: A session can ONLY be pruned if it is unlocked (`manager.lock` is not locked) and has `active_streams == 0` (no active progressive stream tasks).
* **TTL Policy**: Stale sessions are evicted if their idle time exceeds `IDLE_TIMEOUT = 3600` (60 minutes).
