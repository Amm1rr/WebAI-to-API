# Browser Runtime Architecture

This document specifies the architectural contracts, lifecycle invariants, and concurrency models for the WebAI-to-API hardened Playwright runtime.

## 1. Engine State Machine

The `BrowserEngine` operates according to a strict state machine. Transitions into shutdown are terminal.

- **INITIALIZING**: Playwright is starting, or a new browser generation is being launched.
- **HEALTHY**: Browser process is connected, and all `ProviderSession` objects have active, responsive contexts.
- **DEGRADED**: Individual tabs or sessions have failed, but the global browser process and management loop are still functional. Triggers **Self-Healing**.
- **SHUTTING_DOWN**: Terminal state initiated by manual closure, disconnect events, or explicit `close()` calls.
- **CLOSED**: All resources (browser, contexts, loops) have been released.

### State Rules:
- **Terminal Shutdown / Decoupled Intent**: The engine decouples shutdown intention from execution:
  - `shutdown_requested` records lifecycle admission closure and first-writer-owned shutdown source (`application`, `browser-disconnect`, or `manual/internal`).
  - `is_shutting_down` indicates that BrowserEngine terminal teardown is active and blocks recovery while active.
  - `_shutdown_started` is the internal idempotency guard for `close()` execution. It is managed by `close()` under `management_lock`.
- **Application Shutdown**: `ApplicationServer.handle_exit()` marks application shutdown intent before delegating to Uvicorn. Uvicorn drains connections; FastAPI lifespan later calls `BrowserEngine.close(source="application")`.
- **No Resurrection**: A `CLOSED` engine can never transition back to `HEALTHY`. A new process instance must be created.
- **Enforcement**: Any call to `ensure_healthy()` during `SHUTTING_DOWN` or `CLOSED` must raise `RuntimeError("Browser engine is shutting down")`.

## 2. Ownership & Lifecycle Authority

The runtime follows a strict ownership hierarchy. Resource cleanup must cascade down this chain.

1. **BrowserEngine**: Global singleton. Owns the active `BrowserRuntime`, the Browser handle, browser generation, shutdown intent/source, provider-session registry, and terminal lifecycle orchestration.
2. **BrowserRuntime**: Browser-process launch mechanics only. Owns the concrete driver instance and executes browser-process launch, connection, and close mechanics. Selected through `create_browser_runtime()` based on `[Browser] runtime`; the only supported value is `playwright`, producing `PlaywrightChromiumRuntime`. `BrowserRuntime` must NOT own lifecycle/recovery/shutdown policy.
3. **ProviderSession**: Created per provider. Owns the `BrowserContext`, `keepalive_page`, `PersistentTab` pages, lifecycle tasks, and active-request abort/signal handles.
   - **Page Ownership**: `ProviderSession` is solely responsible for the creation, monitoring, and cleanup of its `keepalive_page`.
   - **Separation of Concerns**: `BrowserEngine` must not manipulate the `keepalive_page` directly outside of session teardown orchestration.
4. **ManagedPage**: Request-scoped lease. Owns exactly one semaphore permit and potentially one `PersistentTab` lease.
5. **PersistentTab**: Long-lived browser page. Owns its individual `_lock` and state.
6. **BrowserRequestExecutor**: Owns one request lifecycle state, terminal event/error, request task, observer/queue tasks, and lease release through request cleanup.

### 2.1 BrowserRuntime Contract

`BrowserRuntime` is the launch-mechanics boundary between `BrowserEngine` and the concrete browser driver. It exposes:

- `start()`: initialize the underlying browser driver.
- `launch_browser()`: launch a fresh browser instance and return its handle.
- `bind_disconnect()`: register a callback for unexpected browser disconnect.
- `is_browser_connected()`: report whether the browser transport is still connected.
- `close_browser()`: best-effort, never-raising close of a browser instance.
- `stop()`: tear down the driver; best-effort and idempotent.

Ownership invariants:

- **Mechanics Only**: A `BrowserRuntime` never initiates recovery or shutdown. Lifecycle authority (generation, shutdown, session coordination) stays with `BrowserEngine`.
- **Current Implementation**: `PlaywrightChromiumRuntime` is the sole implementation, selected by `create_browser_runtime()` when `[Browser] runtime = playwright`. No other runtime value is supported; configuring one raises `ValueError`.
- **Not an API Abstraction**: `BrowserRuntime` does NOT abstract page, context, or `PersistentTab` APIs. `ProviderSession` continues to own `BrowserContext` creation/closure, page/session resources, `storage_state` persistence, and conversation/session state. Runtime selection does not change that boundary.

### 2.2 Authority Rules
- **Teardown Authority**: `BrowserEngine.close()` is the ONLY authoritative entry point for terminal shutdown.
- **Parent-Child Teardown**: ProviderSession resources close before the parent Browser, and the Browser closes before the runtime stops the driver, during both replacement and terminal shutdown.
- **Runtime Delegation**: `BrowserEngine` performs browser-process lifecycle decisions but executes the mechanics through the `BrowserRuntime` boundary (e.g., `runtime.stop()`, `runtime.start()`, `runtime.launch_browser()`, `runtime.close_browser()`, `runtime.bind_disconnect()`). It does not drive Playwright launch/stop mechanics directly.
- **Context Closure Authority**: Arbitrary callers and provider adapters must not close the context directly. ProviderSession lifecycle cleanup is the authorized BrowserContext owner.
- **Cleanup Ownership**: `ManagedPage` is responsible for releasing its own permits and leases, even during request cancellation (must be wrapped in `asyncio.shield`).

## 3. Zombie Chromium & Window Liveness

A critical production failure mode was discovered: `browser.is_connected()` is **NOT** an authoritative signal for visible window liveness.

- **The Problem**: If a user manually closes the Chromium window, the browser process may remain alive headlessly (as a "zombie"). Global Playwright events may not fire immediately.
- **The Solution**: The `keepalive_page` is the canonical liveness signal. If `keepalive_page.is_closed()` returns `True`, the window is gone.
- **Hardening**: `ProviderSession.is_alive` must verify `keepalive_page` status. Loss of this page triggers a transition to `SHUTTING_DOWN`, preventing unintended browser recreation loops.

## 4. Recovery Boundary

The system distinguishes between recoverable transient failures and non-recoverable terminal signals.

### Recoverable (Triggers Self-Healing):
- Transient DOM failures or element timeouts.
- Isolated renderer crashes or "page crashed" events.
- Individual tab state corruption.
- Request-level timeouts.

### Non-Recoverable (Triggers Terminal Shutdown):
- Manual browser window closure.
- Unexpected closure of the current `BrowserContext`.
- Global `disconnected` lifecycle event from Playwright.
- System-wide resource exhaustion or engine shutdown initiation.

Intentional BrowserContext closure performed by ProviderSession lifecycle cleanup is not a terminal failure.

**Philosophy**: The system prefers terminal shutdown over aggressive recreation when the user-visible interface is terminated.

## 5. Concurrency & Locking

### 5.1 Lock Hierarchy
To prevent deadlocks, locks must always be acquired in this order. Acquiring locks out-of-order is strictly **forbidden**, as violating this contract introduces deterministic deadlocks:
1. `BrowserEngine.management_lock` (Global orchestration)
2. `ProviderSession.init_lock` (Session setup/recovery)
3. `ProviderSession._cleanup_lock` (Serialized session cleanup)
4. `ProviderSession.registry_lock` (Registry lookups/mutations)
5. `PersistentTab._lock` (Individual tab operations)

`conversation_lock` is independent. Production code has no `init_lock` to `management_lock` acquisition path. `close_resources()` must not acquire `init_lock`; `_setup_locked()` assumes management and init locks are held, `_setup()` acquires them in order, and `_ensure_healthy_browser()` assumes management-lock ownership.

### 5.2 Browser Replacement
Unhealthy-browser replacement follows this order:

1. Detect unhealthy Browser under `management_lock`.
2. Clean ProviderSessions bound to old lifecycle/resources and wait for in-progress cleanup.
3. Close or skip old Browser via the runtime, then stop the driver via the runtime.
4. Start the driver and launch a new Browser through the runtime.
5. Increment `browser_generation` only after successful Browser launch.
6. Set up ProviderSession contexts against the new generation.

Failed launch leaves the generation unchanged. ProviderSessions observe and store `last_browser_generation`; they do not choose generation values. Context callbacks from generation N cannot terminate generation N+1.


## 6. Async Safety Constraints

- **Event Loop Teardown**: Callback scheduling in event listeners must use `asyncio.get_running_loop().create_task()` wrapped in `try/except RuntimeError` to avoid crashes during late-stage interpreter shutdown.
- **Fire-and-Forget**: Shutdown handlers must be non-blocking. Schedule the teardown task and return immediately to allow Playwright's event loop to progress.
- **Cleanup Serialization**: `ProviderSession._cleanup_lock` detaches the context before awaiting its close, makes repeated/concurrent cleanup safe, and treats already-disconnected contexts as benign. Unexpected close failures remain visible. Active cleanup counts as lifecycle-active for replacement decisions.
- **Task Ownership**: ProviderSession tracks context-close callback, recovery, recovery-wrapper, and orphan-cleanup tasks. BrowserEngine tracks disconnect-triggered close tasks. Project-owned task exceptions are retrieved/logged; request observer and queue tasks remain request-owned and are awaited or cancelled.
- **Terminal Request Drain**: Application shutdown rejects new admissions, allows existing requests up to the 15-second drain deadline, then triggers request-owned abort callbacks. Request cleanup releases leases and semaphores; BrowserEngine never mutates lease counters.

## 7. Context Close Semantics

- **Intentional close**: ProviderSession lifecycle cleanup must not trigger terminal browser shutdown.
- **Stale callback**: A callback for an old context or generation is ignored when identity or generation is no longer current.
- **Unexpected current close**: A current-generation/current-context close without shutdown intent or an intentional marker triggers browser-disconnect terminal behavior.
- **Application shutdown**: Current-context closure is expected shutdown activity and must not start crash recovery or duplicate shutdown orchestration. Active requests still receive an immediate terminal browser-disconnect signal if their browser resource disappears.

## 8. Terminal Browser Cleanup

- If public Browser state confirms disconnection, explicit browser close is skipped.
- A public Playwright close error is benign only when state confirms disconnection afterward.
- The known generic Playwright transport-close signature
  `"Connection closed while reading from the driver"` is also benign only when
  post-error state confirms disconnection. Other generic close exceptions remain
  warning-worthy.
- If Browser remains connected, close failure remains a warning.
- Browser cleanup always precedes driver stop and uses no private Playwright exception or API. Both are executed through the `BrowserRuntime` boundary (`close_browser()`, `stop()`).

## 9. AI Agent Rules

AI Agents working on this runtime must adhere to these strict constraints to prevent architectural drift:

1. **No Silent Shutdowns**: Never return silently from `ensure_healthy` during shutdown; always raise `RuntimeError`.
2. **Authoritative Liveness**: Never use `browser.is_connected()` alone; always check `keepalive_page.is_closed()`.
3. **Lazy Logging**: Use lazy `%s` formatting for `DEBUG` logs in hot paths (callbacks/loops) to avoid interpolation overhead.
4. **Ordering Invariant**: Always follow the documented Lock Hierarchy.
5. **Ownership Integrity**: Never bypass `ManagedPage` for resource release.
6. **Shutdown Immutability**: Never attempt to re-enable recovery or "self-healing" once `is_shutting_down` is set.
