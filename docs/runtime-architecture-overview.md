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
- **Atomic Persistence:** Session states are persisted using a write-sync-replace strategy to guarantee state integrity during power loss or crashes.

### 2. Browser Engine (`BrowserEngine`)
- **Active Lifecycle Orchestration:** A global singleton managing the active Chromium process and coordinating cross-provider synchronization. Recovery is valid only within an active engine lifecycle; it is NOT a resurrection authority after terminal shutdown begins.
- **Generation Invalidation:** Tracks browser process generations to automatically invalidate stale contexts, `PersistentTab` objects, active leases, cached page references, and request-scoped bridge state after a process restart or fatal disconnect.
- **Terminal Shutdown Authority:** The authoritative coordinator for irreversible shutdown. It ensures all background activity is halted and requests are drained before process termination.

### 3. Managed Resource Lifecycle
- **ManagedPage & Lease Ownership:** Every request operates within a `ManagedPage` wrapper, which owns a dedicated semaphore permit and a `PersistentTab` lease. Raw page lifecycle management outside this wrapper is strictly forbidden.
- **Deterministic Cleanup:** Release semantics are idempotent, best-effort, cancellation-safe, and shielded via `asyncio.shield` to ensure that resource release is guaranteed even during request cancellation.

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
- **New Adapters:** Native support for ChatGPT Web, Claude Web, and Grok.
- **Multi-Account Pooling:** Support for cycling through multiple authenticated sessions per provider.
- **Session Persistence:** Full integration of `conversation_id` to maintain chat history across API calls.

### Phase 3: Infrastructure (Long-Term)
- **Universal Provider SDK:** A unified framework for adding new web-based AI models.
- **Distributed Browser Farm:** Ability to offload browser contexts to separate nodes.
- **Auto-Auth Solvers:** Automated handling of common login challenges and "What's new" popups.

---

## Relationship to Detailed Runtime Contracts

This document provides a high-level strategic overview. Detailed behavioral guarantees and implementation invariants are codified in the following specifications. **The runtime contracts are authoritative; if this overview conflicts with a detailed contract, the specific contract document takes precedence.**

- **[Concurrency Model](concurrency-model.md)**: Semaphore ownership, lock hierarchy, and cancellation safety.
- **[Provider Contract](provider-contract.md)**: Ownership boundaries, poisoning rules, and escalation semantics.
- **[Streaming Pipeline](streaming-pipeline.md)**: Event flow, normalization, and rewrite-resilience.
- **[Lifecycle and Recovery](lifecycle-and-recovery.md)**: State transitions, generations, and authoritative recovery.

---

## Operational Guide

### 1. Manual Authentication (Session Setup)
The API requires an authenticated browser session. Run the verifier to log in:
```bash
poetry run python verify_login.py
```

### 2. Using the API
Requests use the `playwright/` model prefix. Each request opens a new tab in the shared window:
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
