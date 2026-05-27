# WebAI-to-API: The Browser-Native AI Runtime

> **Status:** Production-Hardened PoC (Playwright Integration)  
> **Vision:** A universal runtime layer for browser-based AI systems.

## Project Overview

**WebAI-to-API** is evolving from a simple Gemini web wrapper into a generalized **"Web AI Runtime"**. The project's core mission is to convert browser-based AI chat applications (Gemini, ChatGPT, Claude, etc.) into stable, developer-friendly, and production-ready APIs.

Instead of relying on fragile reverse-engineered HTTP endpoints, this project leverages **Browser Automation (Playwright)** to drive the actual Web UIs, ensuring maximum compatibility and resilience against protocol changes.

---

## Core Architecture (The Runtime)

The project is built on a modular, provider-agnostic architecture designed for high availability and resource isolation:

### 1. Browser Engine (`src/app/services/browser/engine.py`)
- **Generation-Based Context Rotation:** Implements a self-healing mechanism that rotates browser contexts if they become unhealthy, without interrupting active streams.
- **Concurrency Control:** A global semaphore-based system that limits the number of active browser tabs (default: 5) to prevent memory exhaustion.
- **Persistent Contexts:** Uses local data directories to maintain authenticated sessions (Google login, etc.) across server restarts.
- **Docker Ready:** Configured with specific Chromium flags for stability in containerized environments.

### 2. Provider Adapters (`src/app/services/providers/`)
- **Isolation-Safe Execution:** Each request runs in its own isolated browser page (tab), preventing conversation contamination.
- **Resilient Locators:** Uses semantic ARIA roles and structural markers to interact with Web UIs, making it resilient to CSS/obfuscation changes.
- **GeminiPlaywrightProvider:** The flagship implementation for Google Gemini Web.

### 3. Stream Normalization Layer
- **MutationObserver Extraction:** Injects JS observers into the browser to capture text deltas in real-time.
- **Rewrite-Resilient Diffing:** Detects when the UI re-renders markdown or code blocks, ensuring the stream output remains consistent and duplication-free.
- **Non-Blocking Bridge:** Uses bounded queues and non-blocking callbacks to ensure the browser's message loop is never stalled by the Python backend.

---

### 4. Production Safeguards
- **Strict Semaphore Ownership:** Uses a stateful `RequestState` to guarantee semaphore permits are never leaked or over-released.
- **Cancellation Propagation:** If a client disconnects, the runtime explicitly triggers the UI "Stop" button and cleans up the browser tab immediately.
- **Self-Healing:** Automatically recovers from Chromium crashes or UI freezes.

---

## Strategic Roadmap

### Phase 1: MVP & Hardening (Current)
- [x] Stable Playwright integration for Gemini Web.
- [x] Production-grade lifecycle management and cleanup.
- [x] Concurrency and isolation safety.
- [x] Basic session persistence via manual login.

### Phase 2: Orchestration & Expansion (Medium-Term)
- **Multi-Provider Support:** Add native adapters for ChatGPT Web, Claude Web, and Grok.
- **Automated Auth Management:** Automated cookie rotation and session validation for all providers.
- **Session Registry Migration:** Refactor the existing `SessionRegistry` to manage Playwright Page persistence (Conversation IDs).

### Phase 3: Infrastructure & Ecosystem (Long-Term)
- **Universal Provider Abstraction:** A unified SDK for any browser-based AI.
- **Browser Farm Integration:** Support for distributing browser contexts across multiple nodes.
- **BYOS Infrastructure:** Position as the premier "Bring Your Own Subscription" (BYOS) gateway for developers.

---

## Operational Guide

### 1. Manual Authentication
Before the API can drive the browser, you must log in once to save the persistent context:
```bash
poetry run python verify_login.py
```

### 2. Using the API
Requests to the Playwright runtime use the `playwright/` model prefix:
```bash
curl -X POST http://localhost:6969/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "playwright/gemini",
    "messages": [{"role": "user", "content": "Explain your new architecture."}],
    "stream": true
  }'
```

### 3. Configuration (`config.conf`)
The `[Playwright]` section allows tuning for your environment:
- `headless`: Toggle visible browser (default `false` for debugging).
- `max_concurrent_pages`: Limit concurrent tabs.
- `navigation_timeout`: Timeout for page loads.

---

## Note for AI Agents
When working on this project, prioritize **lifecycle correctness** and **resource isolation**. Every request must be strictly owned by a `RequestState` and guaranteed to clean up its browser resources. Avoid any changes that introduce shared state between active browser pages unless explicitly requested.

---

## License
This project is open-source under the [MIT License](LICENSE).
