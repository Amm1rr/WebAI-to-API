# WebAI-to-API: The Browser-Native AI Runtime

> **Status:** Hardened Browser Lifecycle  
> **Vision:** A specialized runtime layer that converts browser-based AI into high-availability APIs.

## Project Overview

**WebAI-to-API** is a **"Web AI Runtime"** designed to bridge browser-based AI interfaces (Gemini, ChatGPT, Claude) with standard developer workflows via OpenAI-compatible APIs. 

By leveraging **Playwright**, the project drives real browser runtimes rather than reverse-engineering fragile internal protocols. This ensures strong resilience against web UI updates and provides a strongly-governed runtime layer for browser-native LLM integration.

---

## Core Architecture

The architecture is built for **isolation**, **concurrency safety**, and **lifecycle determinism**:

### 1. Provider-Scoped Sessions (`ProviderSession`)
- **Authoritative Ownership:** Each provider session owns its dedicated `BrowserContext`, a `keepalive_page` for liveness monitoring, and the background loops (`reaper`, `autosave`, `eviction`) governing its state.
- **Session-Scoped Recovery:** `ProviderSession` is the authoritative owner of context recreation and tab invalidation logic.
- **Browser State Persistence:** Browser authentication state persistence is controlled by dedicated auth/bootstrap flows. Conversation continuity is provider/backend-specific and is not uniformly owned by `ProviderSession`.

### 2. Browser Engine (`BrowserEngine`)
- **Active Lifecycle Orchestration:** A global singleton managing the active Chromium process and coordinating cross-provider synchronization. Recovery is valid only within an active engine lifecycle; it is NOT a resurrection authority after terminal shutdown begins.
- **Generation Invalidation:** Tracks browser process generations to automatically invalidate stale contexts, `PersistentTab` objects, active leases, cached page references, and request-scoped bridge state after a process restart or fatal disconnect. Newly created sessions are not associated with any generation until their first successful context initialization.
- **Terminal Shutdown Authority:** The authoritative coordinator for irreversible shutdown. It ensures all background activity is halted and requests are drained before process termination.

### 3. Managed Resource Lifecycle
- **ManagedPage & Lease Ownership:** Every request operates within a `ManagedPage` wrapper, which owns a dedicated semaphore permit and a `PersistentTab` lease. Raw page lifecycle management outside this wrapper is strictly forbidden.
- **Deterministic Cleanup:** Release semantics are idempotent, best-effort, cancellation-safe, and shielded via `asyncio.shield` to ensure that resource release is guaranteed even during request cancellation.

### 4. Conversation Continuity Models
- **Gemini WebAPI:** Uses SQLite-backed conversation snapshots to restore serialized `ChatSession` state across process restarts.
- **Gemini Playwright:** Uses provider-side Gemini conversation URLs and in-memory `PersistentTab` reuse. It does not use SQLite snapshots for normal conversation continuity.
- **Stateless Providers:** Some providers, such as Atlas, forward each request independently and do not persist `conversation_id` state locally.

### 5. Authentication Ownership Model
- **Discovery:** `AuthLoader` discovers available auth material only. Discovery includes provider config cookies, legacy cookie configuration, JSON storage state, and browser state where applicable.
- **Selection:** Provider-specific selectors own priority ordering and fallback sequencing. For Gemini, `GeminiAuthSelector` enumerates `[Gemini]` cookies, legacy `[Cookies]` cookies, then `runtime/auth/gemini.json`.
- **Validation and Activation:** Backend implementations decide whether a selected candidate is usable. Gemini WebAPI validates cookies through account status evaluation and activates the direct client, including guest fallback decisions. Gemini Playwright activates storage state through browser-context setup.
- **Caching and Orchestration:** `AuthManager` owns auth status caching, `/v1/auth/status` refresh orchestration, login triggering, and post-login recovery orchestration. It does not own provider-specific source selection or backend activation.
- **Compatibility:** Legacy `[Cookies]` remains supported. `GeminiAuthStateLoader.load_auth_state_with_fallback()` is retained as a deprecated compatibility path, not as the primary runtime selection mechanism.

---

## Architectural Principles

- **Fail-Fast Ownership Validation:** Every browser operation must verify lease validity before execution.
- **No Resurrection after Shutdown:** Once a terminal shutdown begins, the active engine lifecycle cannot be resurrected.
- **Strict Authority Boundaries:** Providers escalate failures; sessions and engines execute recovery.
- **Deterministic Teardown Ordering:** Cleanup must follow a strict sequence to prevent late-event races.
- **No Silent Stream Corruption:** Queue overflows, state mismatches, or out-of-order events result in terminal request failure rather than silent data loss.
- **Isolation over Aggressive Reuse:** Stale, poisoned, or crashed runtime artifacts are permanently invalidated rather than heuristically repaired.

---

## Technical Safeguards

- **Rewrite-Resilient Streaming:** Normalizers calculate text suffixes to prevent duplicate SSE output during browser-side UI polishing.
- **Integrity-First Streaming Pipeline:** Ensures ordered request-scoped bridge event processing, deterministic request-scoped bridge cleanup, and terminal failure semantics on bounded queue overflow.
- **Zombie Chromium Protection:** Uses `keepalive_page` status as the authoritative signal for visible window liveness, preventing unintended recreation loops after manual closure.
- **Safe Teardown Scheduling:** Event-loop-aware task scheduling prevents "no running event loop" errors during late-stage shutdown.

---

## Strategic Roadmap

### Phase 1: Hardened Foundation (Current)
- [x] Authoritative lifecycle orchestration and terminal shutdown.
- [x] Generation-aware lease invalidation.
- [x] Cancellation-safe resource cleanup with `asyncio.shield`.
- [x] Multi-tab concurrency management via `ManagedPage`.

### Phase 2: Provider Expansion (Medium-Term)
- **New Adapters:** Native support for ChatGPT Web, Claude Web, and Grok via specialized browser-native adapters.
- **Multi-Account Pooling:** Support for cycling through multiple authenticated sessions per provider.
- **Conversation Persistence Expansion:** Extend and standardize provider/backend-specific `conversation_id` semantics where useful, while preserving existing WebAPI snapshot-backed and Playwright URL-backed continuity models.

### Phase 3: Infrastructure (Long-Term)
- **Universal Provider SDK:** A unified framework for adding new logical providers and adapters.
- **Distributed Browser Farm:** Ability to offload browser contexts to separate nodes.
- **Auto-Auth Solvers:** Automated handling of common login challenges and "What's new" popups.

---

## Relationship to Detailed Runtime Contracts

This document provides a high-level strategic overview. Detailed behavioral guarantees and implementation invariants are codified in the following specifications. **The runtime contracts are authoritative; if this overview conflicts with a detailed contract, the specific contract document takes precedence.**

- **[Concurrency Model](concurrency-model.md)**: Semaphore ownership, lock hierarchy, and cancellation safety.
- **[Provider Contract](provider-contract.md)**: Ownership boundaries for logical providers and their technical adapters, poisoning rules, and escalation semantics.
- **[Streaming Pipeline](streaming-pipeline.md)**: Event flow, normalization, and rewrite-resilience.
- **[Error Policy](error-policy.md)**: Runtime error semantics, classification boundaries, and recovery authority.
- **[API Contract](api-contract.md)**: Authoritative API surface definitions, persistence guarantees, and endpoint classifications.
- **[Lifecycle and Recovery](lifecycle-and-recovery.md)**: State transitions, generations, and authoritative recovery.
- **[Docker Deployment Model](docker-deployment.md)**: Containerization, environment modes, and volume persistence guarantees.

---

## Operational Guide

### 1. Manual Authentication (Session Setup)
The API requires an authenticated browser session for browser-native execution. While the server can often retrieve cookies from a local browser automatically, you can explicitly trigger a headful login workflow via the API:

```bash
# Trigger the browser-based login workflow
curl -X POST http://localhost:6969/v1/auth/login

# Monitor the login status
curl http://localhost:6969/v1/auth/status
```

### 2. Using the API
By default, the `gemini` provider uses the `webapi` execution adapter. To force browser-native execution, use the `playwright/` model prefix:
```bash
curl -X POST http://localhost:6969/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "playwright/gemini",
    "messages": [{"role": "user", "content": "Hello! List your architecture features."}],
    "stream": true
  }'
```

### 3. Configuration (`config.conf`)
- `headless`: Set to `false` to see the browser window and tabs in real-time.
- `max_concurrent_pages`: Controls the maximum simultaneous request tabs per provider.
- `chunk_timeout`: Maximum wait time for incremental stream data.

---

## Note for AI Agents
Strictly adhere to the authoritative ownership and lock hierarchy specified in the Runtime Contracts. Never bypass the `ManagedPage` resource owner, and ensure all cleanup logic is idempotent, best-effort, and shielded. Terminal shutdown is an irreversible state transition. Runtime components must fail fast rather than attempting lifecycle resurrection. Raw Playwright page ownership, manual semaphore handling, or out-of-band lifecycle recovery are forbidden.

---

## License
This project is open-source under the [MIT License](LICENSE).
