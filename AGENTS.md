# WebAI-to-API: Agent & Contributor Guide

This document specifies the architectural governance, runtime contracts, and operational guidelines for WebAI-to-API. It is the authoritative guide for contributors and AI agents working on the browser-native runtime.

> **Scope of this Document**: This file serves as a contributor governance guide. Detailed behavioral guarantees and technical invariants are codified in the [Runtime Contract Map](#6-runtime-contract-map) inside `docs/`, which remain the normative authority for the system. The runtime contracts primarily govern the hardened Playwright runtime layer.

## Mandatory Architectural Directives

* **Architectural Consistency:** Preserve the established runtime architecture, ownership boundaries, and lifecycle invariants.
* **Clean & Maintainable Code:** Prefer clarity, determinism, and maintainability over clever or overly abstract solutions.
* **No Unscoped Refactors:** Avoid unrelated rewrites or architectural changes unless explicitly required.
* **Documentation Consistency:** Keep comments, structured logs, and documentation aligned with the implemented behavior whenever modifying logic.
* **Pragmatic Engineering:** Avoid unnecessary over-engineering. Implement the smallest solution that fully satisfies the runtime contracts and operational requirements.
* **Regression Safety:** New changes must preserve existing runtime guarantees, API behavior, and operational stability unless an intentional breaking change is explicitly requested.
* **Evidence-Driven Changes:** Base technical decisions on actual code paths, runtime behavior, and authoritative specifications rather than assumptions.
* **Structural Discipline:** Follow the established project structure, ownership hierarchy, and authoritative runtime contracts in `docs/`.

## 1. Technical Vision

**WebAI-to-API** is a specialized **Web AI Runtime** that converts browser-based AI interfaces into high-availability, OpenAI-compatible APIs. Unlike legacy systems that rely on reverse-engineered internal protocols, this runtime drives real browser sessions via Playwright, ensuring strong resilience against web UI updates and providing a deterministic and strongly-governed runtime layer for browser-native LLM integration.

---

## 2. Core Runtime Architecture

The system operates according to a strict ownership hierarchy and state machine.

### 2.1 Component Hierarchy
1. **BrowserEngine**: Global singleton. Authoritative orchestrator for the core Chromium process and coordinator for terminal shutdown.
2. **ProviderSession**: Created per provider (Gemini, etc.). Authoritative owner of the `BrowserContext`, the `keepalive_page`, and session-scoped recovery logic (context recreation, tab invalidation).
3. **ManagedPage**: Request-scoped resource container. Authoritatively owns exactly one semaphore permit and one `PersistentTab` lease.
4. **PersistentTab**: Long-lived browser page in the session registry. Owns its individual `_lock` and internal state.

### 2.2 Lifecycle & Ownership
- **Generation Invalidation**: Tracks browser process generations to automatically invalidate stale contexts, `PersistentTab` objects, active leases, cached references, and request-scoped bridge state after a browser generation rollover or fatal disconnect.
- **Terminal Shutdown**: Once `BrowserEngine` initiates shutdown, the active engine lifecycle cannot be resurrected. Runtime components must fail fast and may never re-initialize the browser after terminal shutdown begins.
- **Deterministic Teardown**: Request cleanup MUST follow a strict sequence (Observers -> Tasks -> Callbacks -> Queues -> Leases) to prevent late-event races.

---

## 3. Concurrency & Locking

### 3.1 Lock Hierarchy
To prevent deadlocks, locks must be acquired strictly in this order. Acquiring out-of-order is strictly forbidden:
1. `BrowserEngine.management_lock` (Global orchestration)
2. `ProviderSession.init_lock` (Session setup/recovery)
3. `ProviderSession.registry_lock` (Synchronous registry mutations only)
4. `PersistentTab._lock` (Individual tab operations)

**Discipline**: `registry_lock` must NEVER be held across `await` points or long-running Playwright operations. Violating this scope discipline risks global request starvation.

### 3.2 Semaphore & Lease Semantics
- **Exclusivity**: Active browser operations require a valid lease on a `PersistentTab`.
- **Mandatory Shielding**: Resource release (locks, permits, registry entries) must be performed via `asyncio.shield` to guarantee completion during request cancellation.
- **Idempotency & Best-Effort**: Cleanup paths and `ManagedPage.close()` must be idempotent and best-effort. Failures in one step must not block subsequent resource release.

---

## 4. Provider & Streaming Contracts

### 4.1 Required Hooks
Providers must implement:
- **Initialization**: Call `session.acquire_lease()` and register lifecycle listeners.
- **Observer Injection**: Inject stream extraction scripts and wait for the `ready` signal.
- **Hardened Submission**: Use the adapter to submit prompts and wait for authoritative bridge signals.
- **Mandatory Cleanup**: Execute the deterministic teardown sequence in a `finally` block.

### 4.2 Streaming Invariants
- **Rewrite-Resilience**: Normalizers must calculate text suffixes to prevent duplicate SSE output during re-renders.
- **Ordered Processing**: Bridge events for a single `request_id` must be processed in strict FIFO order.
- **Failure Isolation**: Bounded queue overflow or request-level errors result in terminal request failure but must not poison the broader `ProviderSession`.
- **Bridge Cleanup**: Request-scoped bridge state MUST be deterministically removed after stream termination.

---

## 5. Failure & Recovery Boundaries

- **Self-Healing (Recoverable)**: Transient transport or UI errors (timeouts, selector fails) trigger session-scoped recovery. Providers identify and escalate these conditions; `ProviderSession` executes the authoritative recovery.
- **Terminal Shutdown (Non-Recoverable)**: Manual window closure or global disconnect triggers irreversible engine shutdown via `BrowserEngine`.
- **Window Liveness**: `browser.is_connected()` is NOT an authoritative signal for visible window liveness. Runtime components MUST verify `keepalive_page.is_closed()`.
- **Poisoned Pages**: Crashed or corrupted tabs are permanently invalidated and never reused.

---

## 6. Runtime Contract Map

Authoritative technical invariants and behavioral guarantees are codified in the following specifications. **If any summary in this guide conflicts with a detailed contract document, the contract document takes precedence.**

- **[Concurrency Model](docs/concurrency-model.md)**: Governs lock hierarchy, semaphore ownership, lease invalidation, cancellation safety, and background loop authority.
- **[Lifecycle and Recovery](docs/lifecycle-and-recovery.md)**: Governs engine state machine, terminal shutdown invariants, generation rollover semantics, and authoritative recovery boundaries.
- **[Provider Contract](docs/provider-contract.md)**: Governs provider adapter responsibilities, page poisoning rules, page ownership boundaries, and escalation semantics.
- **[Streaming Pipeline](docs/streaming-pipeline.md)**: Governs SSE ordering guarantees, rewrite-resilient normalization, bridge callback lifecycle, and queue overflow semantics.
- **[Error Policy](docs/error-policy.md)**: Governs runtime error semantics, classification boundaries, authoritative recovery, and cancellation-safe cleanup.
- **[API Contract](docs/api-contract.md)**: Governs authoritative API contracts, endpoint classifications (Primary vs Legacy), persistence guarantees, and compatibility boundaries.
- **[Architecture Overview](docs/runtime-architecture-overview.md)**: High-level strategic vision, architectural principles, component relationships, and strategic roadmap.
- **[Docker Deployment Model](docs/docker-deployment.md)**: Governs the containerized execution environment, environment orchestration, and volume persistence policies.

---

## 7. AI Agent Rules (Mandatory Compliance)

AI Agents working on this runtime MUST adhere to these strict constraints:

1.  **Never Bypass ManagedPage**: Raw Playwright page lifecycle management or manual semaphore handling is forbidden.
2.  **No Resurrection**: Never attempt to re-initialize or "self-heal" after `is_shutting_down` is set; the active engine lifecycle cannot be resurrected.
3.  **Strict Generation Invalidation**: Always invalidate all context references, `PersistentTab` objects, and active leases on generation mismatch; never attempt reuse of stale pages.
4.  **No Lock-Order Violations**: Adhere to the 4-level lock hierarchy; never `await` under `registry_lock`.
5.  **Never Reuse Poisoned Pages**: Invalidation of crashed or corrupted tabs is terminal and irreversible.
6.  **No Orphan Tasks**: Ensure all async tasks are tracked, request-scoped, and cleaned up during teardown.
7.  **Fail Fast**: Raise `RuntimeError` immediately if liveness invariants are violated or terminal shutdown is in progress.
8.  **Lease Ownership**: Verify lease validity before every lease-sensitive operation, tab mutation, or release.

---

## 8. Development & Operations

### 8.1 Key Directory Structure
- `src/app/services/browser/`: Core engine, session, and tab management.
- `src/app/services/providers/`: Provider-specific implementation logic.
- `docs/`: authoritative runtime contracts (normative specs).

### 8.2 Core Commands
- **Run Server**: `poetry run python src/run.py`
- **Verify Login**: `poetry run python verify_login.py` (authoritative setup)
- **Run Tests**: `PYTHONPATH=src pytest`
