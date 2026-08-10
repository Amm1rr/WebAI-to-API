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
- **Terminal Shutdown / Decoupled Intent**: To prevent diagnostic race conditions and clean up cleanly, the engine decouples shutdown intention from execution:
  - `is_shutting_down` (Public Boolean): Lifecycle intention flag. Declares the *intent* to terminate. Set immediately when any signal is captured or teardown begins to block concurrent work, prevent loop execution, and suppress connection warning logs during event loop closures. External signal handlers or parent process wrappers may choose to set `is_shutting_down = True` on the engine singleton. Such integrations must be validated against the application's shutdown lifecycle.
  - `_shutdown_started` (Private Boolean): `close()` execution guard. Protects the physical teardown actions of closing browser processes and stopping Playwright. It must only be modified inside `close()` under the `management_lock`. Upstream or external systems must never mutate `_shutdown_started`.
- **No Resurrection**: A `CLOSED` engine can never transition back to `HEALTHY`. A new process instance must be created.
- **Enforcement**: Any call to `ensure_healthy()` during `SHUTTING_DOWN` or `CLOSED` must raise `RuntimeError("Browser engine is shutting down")`.

## 2. Ownership & Lifecycle Authority

The runtime follows a strict ownership hierarchy. Resource cleanup must cascade down this chain.

1. **BrowserEngine**: Global singleton. Owns the Playwright process and the `management_lock`.
2. **ProviderSession**: Created per provider. Owns the `BrowserContext`, the `keepalive_page`, and background loops (`reaper`, `autosave`, `eviction`).
   - **Page Ownership**: `ProviderSession` is solely responsible for the creation, monitoring, and cleanup of its `keepalive_page`.
   - **Separation of Concerns**: `BrowserEngine` must not manipulate the `keepalive_page` directly outside of session teardown orchestration.
3. **ManagedPage**: Request-scoped lease. Owns exactly one semaphore permit and potentially one `PersistentTab` lease.
4. **PersistentTab**: Long-lived browser page. Owns its individual `_lock` and state.

### Authority Rules:
- **Teardown Authority**: `BrowserEngine.close()` is the ONLY authoritative entry point for terminal shutdown. 
- **Direct Closure Forbidden**: Contributors must never call `browser.close()` or `context.close()` directly without triggering the engine's shutdown sequence to ensure background loops are cancelled.
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
- Explicit `BrowserContext` closure.
- Global `disconnected` lifecycle event from Playwright.
- System-wide resource exhaustion or engine shutdown initiation.

**Philosophy**: The system prefers terminal shutdown over aggressive recreation when the user-visible interface is terminated.

## 5. Concurrency & Locking

### 5.1 Lock Hierarchy
To prevent deadlocks, locks must always be acquired in this order. Acquiring locks out-of-order is strictly **forbidden**, as violating this contract introduces deterministic deadlocks:
1. `BrowserEngine.management_lock` (Global orchestration)
2. `ProviderSession.init_lock` (Session setup/recovery)
3. `ProviderSession.registry_lock` (Registry lookups/mutations)
4. `PersistentTab._lock` (Individual tab operations)


## 6. Async Safety Constraints

- **Event Loop Teardown**: Callback scheduling in event listeners must use `asyncio.get_running_loop().create_task()` wrapped in `try/except RuntimeError` to avoid crashes during late-stage interpreter shutdown.
- **Fire-and-Forget**: Shutdown handlers must be non-blocking. Schedule the teardown task and return immediately to allow Playwright's event loop to progress.

## 7. AI Agent Rules

AI Agents working on this runtime must adhere to these strict constraints to prevent architectural drift:

1. **No Silent Shutdowns**: Never return silently from `ensure_healthy` during shutdown; always raise `RuntimeError`.
2. **Authoritative Liveness**: Never use `browser.is_connected()` alone; always check `keepalive_page.is_closed()`.
3. **Lazy Logging**: Use lazy `%s` formatting for `DEBUG` logs in hot paths (callbacks/loops) to avoid interpolation overhead.
4. **Ordering Invariant**: Always follow the documented Lock Hierarchy.
5. **Ownership Integrity**: Never bypass `ManagedPage` for resource release.
6. **Shutdown Immutability**: Never attempt to re-enable recovery or "self-healing" once `is_shutting_down` is set.
