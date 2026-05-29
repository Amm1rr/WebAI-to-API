# Error Policy

This document specifies the authoritative error handling model, recovery boundaries, and invariant enforcement protocols for the WebAI-to-API hardened Playwright runtime.

## 1. Purpose & Scope

The **Error Policy** defines the deterministic behavior of the runtime when encountering failures. It establishes the rules for error classification, recovery authority, and lifecycle-safe escalation.

- **Governing Semantics**: This document governs how exceptions are categorized, propagated, and handled across all runtime layers.
- **Authority Boundaries**: It defines which components own the right to execute recovery or initiate shutdown.
- **Relationship**: This is a core runtime contract. If conflicts occur, more specialized runtime contracts (e.g., `concurrency-model.md`) take precedence for subsystem-specific behavior.

---

## 2. Core Error Handling Principles

The following principles are mandatory invariants for the runtime:

- **Fail-Fast Invariant Enforcement**: Invariant violations (e.g., lock order, lease validity) must result in immediate failure rather than attempted workaround.
- **No Silent Recovery**: Recovery actions (e.g., context recreation) must be explicitly logged and never performed silently.
- **Deterministic Failure Semantics**: Similar failure conditions must result in identical, predictable state transitions.
- **Authority-Based Recovery**: Only designated components may execute recovery logic. Providers identify and escalate; Sessions and Engines execute.
- **Cancellation-Safe Cleanup**: Teardown logic must be protected from `CancelledError` via `asyncio.shield` to prevent resource leaks.
- **Isolation over Aggressive Reuse**: Poisoned pages or corrupted states must be permanently invalidated.
- **No Resurrection after Terminal Shutdown**: Once a terminal shutdown begins, all recovery attempts must fail immediately.
- **Explicit Error Classification**: Failures must be categorized by their scope (Request, Tab, Session, Engine) to ensure appropriate escalation.
- **Best-Effort Cleanup**: Partial cleanup failures must not suppress fatal state or prevent the release of critical resources (locks, permits).

---

## 3. Error Classification Model

Failures are formally classified into four levels of impact:

### 3.1 Recoverable Errors
- **Scope**: Transient UI or transport instability where the browser process remains healthy.
- **Examples**: Transient selector failures, navigation timeouts, temporary UI desync, authentication expiry.
- **Protocol**: Providers detect and escalate to `ProviderSession.ensure_healthy()`. The session performs authoritative recovery (e.g., context refresh or tab purge).

### 3.2 Request-Scoped Terminal Errors
- **Scope**: Failures that invalidate a specific request but do not poison the broader session or tab.
- **Examples**: Bounded queue overflow, out-of-order stream events, invalid request-scoped bridge state, lease invalidation during an active operation.
- **Protocol**: The request must fail immediately. The `ManagedPage` performs deterministic cleanup. Reuse eligibility of the underlying `PersistentTab` is determined exclusively by session-level poisoning and lease validity rules.

### 3.3 Session-Scoped Failures
- **Scope**: Structural failures within a specific `ProviderSession`.
- **Examples**: `keepalive_page` loss, persistent context corruption, unrecoverable renderer instability.
- **Protocol**: The session is treated as degraded and invalidates all active leases before executing authoritative session-scoped recovery according to lifecycle ownership rules.

### 3.4 Engine-Scoped Fatal Errors
- **Scope**: Irreversible loss of the global browser process or user interface.
- **Examples**: Manual window closure, browser process disconnect, fatal `BrowserEngine` state corruption, generation rollover invalidation.
- **Protocol**: The runtime enters an irreversible **Terminal Shutdown** state. All future recovery attempts and new requests MUST fail fast with terminal shutdown semantics.

---

## 4. Recovery Authority Boundaries

Ownership of recovery execution is strictly partitioned:

| Component | Responsibility | Recovery Capability |
| :--- | :--- | :--- |
| **Provider** | Detect & Escalate | Identify failures; signal recovery needs to Session. |
| **ProviderSession** | Recovery Authority | Recreate contexts; invalidate tabs; restore session-scoped state. |
| **BrowserEngine** | Shutdown Authority | Manage process lifecycle; initiate irreversible terminal shutdown. |

**Invariants**:
- Providers MUST NEVER recreate browser contexts or processes directly.
- Recovery logic MUST NEVER bypass the authoritative lifecycle ownership of the Session or Engine.
- Shutdown authority belongs exclusively to `BrowserEngine`.

---

## 5. Runtime Exception Semantics

The use of a semantic exception hierarchy is strongly recommended to enable precise escalation:

- `RuntimeInvariantError`: Critical violation of system invariants (e.g., lock order).
- `LeaseInvalidatedError`: Attempt to operate on a lease that is no longer authoritative.
- `TerminalShutdownError`: Operation attempted during or after terminal shutdown.
- `BrowserGenerationMismatchError`: Detected use of artifacts from a previous browser process.
- `PoisonedPageError`: Interaction with a tab marked `DEAD`.
- `StreamIntegrityError`: Detection of corrupted or out-of-order streaming data.
- `QueueOverflowError`: Request-scoped event queue has reached its safety bound.
- `RecoveryRequiredError`: Internal signal for escalation to session-level recovery.

**Invariant**: Exception classification MUST reflect runtime scope and recovery authority boundaries.

**Protocol**: Generic `RuntimeError` usage should be minimized in invariant-sensitive paths in favor of these semantic types.

### 5.1 Escalation Semantics
- **Detection**: Providers are responsible for detecting failures and escalating to the authoritative session layer.
- **Recovery Authority**: `ProviderSession` performs authoritative recovery for session-scoped failures (e.g., context recreation, tab purging).
- **Shutdown Authority**: `BrowserEngine` owns terminal shutdown authority and coordinates global process teardown.
- **Isolation**: Request-scoped failures must not silently escalate into engine shutdown; they must be handled at the lowest possible authority level.
- **Precedence**: Engine-scoped failures invalidate all lower-level recovery paths immediately.
- **Ownership**: Recovery execution must respect lifecycle ownership boundaries and follow the established lock hierarchy.

---

## 6. Cancellation & Cleanup Semantics

Cancellation safety is a core runtime invariant.

- **Shielded Teardown**: All resource release (locks, semaphore permits, bridge state) MUST be wrapped in `asyncio.shield`.
- **Idempotency**: Cleanup paths and `ManagedPage.close()` MUST be safe for multiple concurrent executions.
- **Non-Swallowing**: `CancelledError` must be propagated after cleanup; it must never be silently suppressed.
- **Best-Effort Success**: Teardown must continue even if individual auxiliary steps (e.g., script detachment) fail. Lock and permit release is mandatory.
- **Task Integrity**: Request-scoped async tasks (observers, generators) must be terminated before communication channels are removed.

---

## 7. Logging & Observability

Runtime integrity failures must be visible and traceable.

- **Contextual Metadata**: Logs MUST contain relevant identifiers: `provider`, `request_id`, `generation`, `tab_id`, and `lease_id`.
- **Invariant Violations**: Violations of the Lock Hierarchy or Ownership Boundaries must be logged as `CRITICAL`.
- **No Silencing**: Swallowing exceptions without a logged technical rationale is forbidden.
- **Severity Discipline**:
    - **DEBUG**: Hot-path events (bridge events, loop iterations).
    - **WARNING**: Recoverable failures and successful session-scoped recovery events.
    - **ERROR**: Request-scoped terminal failures.
    - **CRITICAL**: Session corruption, invariant violations, or engine-scoped fatal errors.

---

## 8. Forbidden Error Handling Patterns

The following patterns are strictly forbidden:

1. **Silent Swallowing**: Broad `except Exception: pass` without logging and justification.
2. **Post-Shutdown Resurrection**: Attempting to re-initialize or self-heal after `is_shutting_down` is `True`.
3. **Lease Reuse**: Attempting to use a tab or lease after invalidation (crash, poisoning, rollover).
4. **Autonomous Recovery**: Recreating browser contexts or processes from within provider logic.
5. **Lock-Order Violations**: Attempting to acquire locks outside the authoritative lock hierarchy, including during recovery or race mitigation.
6. **Unshielded Cleanup**: Performing critical resource release outside of `asyncio.shield`.
7. **Ignoring Generations**: Allowing stale page references to persist across browser process generations.

---

## 9. Relationship to Other Runtime Contracts

This document complements and is supported by the other authoritative specifications:

- **[Concurrency Model](concurrency-model.md)**: Defines the locks and permits that must be safely released during error handling.
- **[Provider Contract](provider-contract.md)**: Defines the poisoning and escalation rules for provider implementations.
- **[Streaming Pipeline](streaming-pipeline.md)**: Defines the integrity rules for event-driven data flow.
- **[Lifecycle and Recovery](lifecycle-and-recovery.md)**: Defines the state transitions triggered by the classifications in this policy.
- **[Architecture Overview](runtime-architecture-overview.md)**: Provides the high-level vision for the hardened runtime.
