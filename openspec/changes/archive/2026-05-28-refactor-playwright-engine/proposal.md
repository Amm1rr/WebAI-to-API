## Why

The current `engine.py` is a monolithic file that tightly couples the singleton browser manager (`BrowserEngine`), isolated provider contexts (`ProviderSession`), leased page resources (`PersistentTab`, `ManagedPage`), and provider-specific details (such as Gemini-specific DOM selectors, authentication, and input injections). This coupling makes the codebase hard to maintain.

To resolve this complexity, this change proposes a strict, behavior-preserving modularization. The refactor is strictly limited to code extraction and concerns separation: relocating tab states, session registries, and provider DOM details into separate files. This minimizes the footprint of `engine.py` without modifying existing concurrency, locking, or streaming pipeline behaviors.

---

## What Changes

- **Concise Provider Adapter**: Introduce a minimal `BaseProviderAdapter` interface restricted solely to:
  - Authentication checks
  - URL/conversation parsing
  - Prompt submission
  - Exposing optional DOM selector/extraction helpers
- **Concise Gemini Adapter**: Relocate Gemini DOM selectors, authentication checks, URL state parsers, and custom Javascript extraction scripts into a concrete `GeminiProviderAdapter`. Relocating browser JS constants into adapter script modules is done purely for organizational separation of vendor-specific DOM artifacts; it does not grant stream orchestration or lifecycle ownership to the adapter.
- **Relocated Resource Management**: Move `PersistentTab` and `ManagedPage` into a dedicated `tab.py` file, preserving all locking models and cancellation shielding invariants.
- **Relocated Session Management**: Move `ProviderSession` and its background sweep loops (`_reaper_loop`, `_eviction_loop`, `_autosave_loop`, and orphan cleanups) into a dedicated `session.py` file.
- **Relocated Engine Orchestration**: Keep only `BrowserEngine` process lifecycle methods and cross-session soft-cap limits in `engine.py`.
- **Simplified Adapter Discovery**: Implement a basic adapter registry mapping providers to resolved sessions without capability negotiation or custom factories.

---

## Capabilities

### New Capabilities
- `modular-browser-engine`: Relocates process management, session environments, tab lifecycles, and provider-specific DOM drivers into separate, dedicated modules, establishing a minimal, non-behavioral provider adapter interface.

### Modified Capabilities
<!-- None: The requirements of existing capabilities (e.g. global-tab-soft-cap) are strictly preserved without any changes to their spec-level behaviors. -->

---

## Architectural Constraints (Guarantees of Behavior Preservation)

During this refactor, the following boundaries are strictly enforced:
- **No orchestration rewrite**: The sequence of prompt delivery, navigation triggers, and request execution remains unchanged.
- **No concurrency model redesign**: Lock acquisition orders, semaphores, and exclusive tab ownership are not modified.
- **No streaming pipeline redesign**: Stream queues, backpressure handling, SSE formatting, and cancellation handling remain entirely orchestrator-owned. The adapter layer does not own stream lifecycle management, queue handling, or stream observer execution.
- **No capability negotiation system**: No dynamic feature checks or dynamic runtime branching are introduced.
- **No behavioral changes during modularization**: Relocated code blocks must be structurally identical to their legacy implementations.

---

## Impact

- **Affected Code**: `src/app/services/browser/engine.py` (to be split), `src/app/services/providers/gemini_playwright.py` (to import from new modules).
- **New Files**: `src/app/services/browser/tab.py`, `src/app/services/browser/session.py`, `src/app/services/browser/base_adapter.py`, `src/app/services/browser/adapters/gemini_adapter.py`, `src/app/services/browser/adapters/scripts/gemini_scripts.py`.
- **APIs**: Zero impact on the external OpenAI-compatible HTTP REST endpoints.
- **Dependencies**: No new third-party Python packages or library changes.
