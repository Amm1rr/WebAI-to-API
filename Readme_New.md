# WebAI-to-API: The Browser-Native AI Runtime

> **Status:** Production-Ready (Hardened Playwright Architecture)  
> **Vision:** A universal runtime layer that converts browser-based AI into high-availability APIs.

## Project Overview

**WebAI-to-API** is a specialized **"Web AI Runtime"** designed to convert browser-based AI interfaces (Gemini, ChatGPT, Claude) into stable, OpenAI-compatible APIs. 

By leveraging **Playwright**, the project bypasses fragile reverse-engineered protocols and instead drives actual browser instances. This ensures maximum resilience against UI updates and protocol changes, providing a "bridge" between the web-native AI world and standard developer workflows.

---

## Core Architecture

The architecture is built for **extreme reliability**, **isolation**, and **concurrency safety**:

### 1. Provider-Scoped Sessions (`ProviderSession`)
- **Complete Isolation:** Each provider (Gemini, ChatGPT, etc.) operates in its own dedicated `BrowserContext`. Cookies, localStorage, and session data are strictly isolated.
- **Tab-Based Strategy:** Requests are processed as isolated **Pages (Tabs)** within the same browser window, significantly reducing memory overhead compared to multi-window models.
- **Atomic Persistence:** Session states are saved using a **Write-Sync-Replace** strategy. This prevents file corruption during unexpected crashes or power failures.

### 2. Browser Engine (`BrowserEngine`)
- **Singleton Orchestration:** Manages the core Chromium process and coordinates multiple provider sessions.
- **Generation Tracking:** Tracks browser process restarts (generations) to automatically invalidate and recreate stale contexts, ensuring zero "zombie" states.
- **Self-Healing Keepalive:** Maintains a permanent, hidden "Keeper Tab" in every context to prevent Chromium from closing the window and to monitor renderer health in real-time.

### 3. Managed Resource Lifecycle
- **ManagedPage Wrapper:** A foolproof resource owner that ensures every browser tab and its associated concurrency permit (Semaphore) is released exactly once, even during critical failures.
- **Graceful Shutdown Drain:** During server shutdown, the engine waits for active requests to finish (Drain period) before persisting state and closing the browser.

---

## Production Safeguards

- **Atomic State Saves:** Uses `fsync` and temporary files to guarantee `state.json` integrity.
- **Fail-Open Auth Checks:** Heuristic detection of login pages and guest modes that avoids unnecessary context resets during transient network blips.
- **Bounded RPC Timeouts:** All browser interactions (typing, clicking, health probes) are wrapped in strict timeouts to prevent the request pipeline from stalling.
- **Rewrite-Resilient Stream:** Injected JS observers detect Gemini UI "polishing" (markdown re-renders) to provide a clean, duplication-free stream.

---

## Strategic Roadmap

### Phase 1: Hardened Foundation (Current)
- [x] Provider-Scoped context isolation.
- [x] Atomic session persistence and corruption safety.
- [x] Self-healing keeper tab for window persistence.
- [x] Multi-tab concurrency management via `ManagedPage`.

### Phase 2: Provider Expansion (Medium-Term)
- **New Adapters:** Add native support for ChatGPT Web, Claude Web, and Grok.
- **Multi-Account Pooling:** Support for cycling through multiple authenticated sessions per provider.
- **Session Persistence:** Full integration of `conversation_id` to maintain chat history across API calls.

### Phase 3: Infrastructure (Long-Term)
- **Universal Provider SDK:** A unified framework for adding new web-based AI models.
- **Distributed Browser Farm:** Ability to offload browser contexts to separate nodes.
- **Auto-Auth Solvers:** Automated handling of common login challenges and "What's new" popups.

---

## Operational Guide

### 1. Manual Authentication (Session Setup)
The API requires an authenticated browser session. Run the smart verifier to log in:
```bash
poetry run python verify_login.py
```
*The script will automatically detect a successful login to Gemini and save the state to disk.*

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
- `max_concurrent_pages`: Controls the maximum number of simultaneous request tabs per provider.
- `chunk_timeout`: Maximum wait time for the next token in a stream.

---

## Note for AI Agents
When modifying this codebase, strictly adhere to the **ManagedPage** ownership model. Manual semaphore management or raw `page.close()` calls outside the wrapper are forbidden. Always prioritize **Fail-Open** logic for health checks to maintain system uptime during transient web UI inconsistencies.

---

## License
This project is open-source under the [MIT License](LICENSE).
