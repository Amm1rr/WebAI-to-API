## Context

The current `src/app/services/browser/engine.py` implements a production-grade async Playwright-based LLM browser driver. However, it mixes Chromium process lifecycle management, provider session settings, persistent conversation registries, background sweep loops, and Gemini-specific DOM interactions.

To resolve this complexity, this design outlines a strict, behavior-preserving modularization. The refactor is strictly limited to code extraction and concerns separation: relocating tab states, session registries, and provider DOM details into separate files. This minimizes the footprint of `engine.py` without modifying existing concurrency, locking, or streaming pipeline behaviors.

---

## Goals / Non-Goals

**Goals:**
- **Lowest Regression Risk**: Optimize for the smallest behavioral and concurrency delta relative to the current working code.
- **Concern Isolation**: Relocate logic into separate modules (`tab.py`, `session.py`, `base_adapter.py`, `adapters/gemini_adapter.py`) to reduce the complexity of the monolithic `engine.py`.
- **Simplest Rollback Path**: Establish rollback-safe phases that enable incremental checkouts and easy verification.

**Non-Goals (Explicit Architectural Assertions):**
- **No orchestration rewrite**: The sequence of prompt delivery, navigation triggers, and request execution remains completely unchanged.
- **No concurrency model redesign**: Lock acquisition orders, semaphores, and exclusive tab ownership are not modified.
- **No streaming pipeline redesign**: Stream queues, backpressure handling, SSE formatting, and cancellation handling remain entirely orchestrator-owned.
- **No capability negotiation system**: No dynamic feature checks or dynamic runtime branching are introduced.
- **No behavioral changes during modularization**: Relocated code blocks must be structurally identical to their legacy implementations.

---

## Decisions

### Decision 1: Intentionally Minimal Adapter Interface

The adapter interface is designed solely as a metadata and DOM-extraction contract. Speculative abstractions (such as streaming chunk interceptors, rate-limiting handlers, or recovery hooks) are explicitly excluded to keep the contract simple and stable.

```python
from abc import ABC, abstractmethod
from typing import Optional
from playwright.async_api import Page

class BaseProviderAdapter(ABC):
    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Returns the vendor name, e.g. 'gemini'."""
        pass

    @abstractmethod
    async def check_authentication(self, page: Page) -> bool:
        """Checks browser context session credentials."""
        pass

    @abstractmethod
    def extract_conversation_id(self, url: str) -> Optional[str]:
        """Extracts the stateful thread ID from the current browser URL."""
        pass

    @abstractmethod
    async def submit_prompt(self, page: Page, prompt: str) -> bool:
        """Injects, types, and sends the prompt text on the browser DOM."""
        pass
```

The concrete `GeminiProviderAdapter` will implement this interface, housing the selectors, click handlers, URL regex matchers, and auth button evaluations currently scattered in `engine.py` and `gemini_playwright.py`.

---

### Decision 2: Concurrency, Locks & `submit_lock` Semantics

To eliminate concurrency delta and regression risks:
- **`submit_lock` remains session-owned and unconditional**: The `submit_lock` continues to be held unconditionally at the session level by `ProviderSession` during prompt filling and clicking. There are no adapter overrides, bypass options, or conditional lock pathways.
- **Registry locking invariants remain identical**: The synchronous, in-memory `registry_lock` must wrap only CPU-bound registry mutations. No asynchronous awaits (such as Playwright evaluations or disk writes) may occur while it is held.
- **Lock Hierarchy Compliance**:
  - `management_lock > init_lock > state_lock > submit_lock > PersistentTab._lock > registry_lock`

---

### Decision 3: Streaming Pipeline & Script Ownership

All streaming pipelines and runtime execution lifecycles remain completely owned by the orchestrator (`GeminiPlaywrightProvider`) and the session layer (`ProviderSession`).

1. **No streaming hooks**: Adapters do not have hooks to intercept, format, or process stream deltas.
2. **Unmodified pipelines**: The stream queue buffer, SSE formatting routines (`format_sse_chunk`), token timing logging (`ttft`), and cancellation stops (clicking the browser Stop button inside `GeneratorExit`) remain strictly untouched.
3. **Script Ownership Clarification**:
   - The relocation of browser Javascript constants (`STREAM_EXTRACTOR_SCRIPT`, `STOP_OBSERVER_SCRIPT`) into an adapter-specific module (`adapters/scripts/gemini_scripts.py`) is done **purely for organizational separation of vendor-specific DOM artifacts**.
   - The adapter layer **does NOT own** stream orchestration, stream lifecycle management, queue handling, SSE formatting, cancellation flow, or the observer execution lifecycle.
   - Script injection and execution timing (calling `page.evaluate` and exposing bridge bindings) remain entirely orchestrator-owned and session-owned.

---

### Decision 4: Registry Discovery & Sessions (Phase 6)

The multi-provider architecture is integrated using a basic, non-intrusive registry:
- **Registry Mapping**: A simple dictionary mapping provider names (e.g. `'gemini'`) to their corresponding adapter instance is maintained inside `BrowserEngine`.
- **Lazy Session Initialization**: The session is only instantiated when a request first resolves a model associated with that provider.
- **Provider Isolation Strategy**: Each provider has its own `BrowserContext` and `asyncio.Semaphore` pool, ensuring that context pollution or a crash in one provider context does not affect others.

---

### Decision 5: Compatibility Invariants (No Regression Checklist)

During refactoring, the following compatibility parameters MUST be preserved exactly:

* **Current API Behavior**: The response payload structure, field configurations, TTFT logging, and error code translations must remain 100% identical.
* **Response Streaming Semantics**: Stream queues, backpressure handling, and SSE formatting must be preserved exactly.
* **Decoupled Script Execution Invariant**: Relocating browser JS constants (`STREAM_EXTRACTOR_SCRIPT`, `STOP_OBSERVER_SCRIPT`) into adapter-specific script modules MUST NOT alter execution ordering, injection timing, cancellation timing, or stream observer lifecycle semantics.
* **Cancellation Behavior**: The `asyncio.shield` protections inside request cleanups (`_do_cleanup`) and lease closures (`ManagedPage.close()`) must remain intact to prevent lock/semaphore leaks during socket drops.
* **Conversation Reuse Behavior**: URL target generation, lazy conversation registrations on navigation match, and stateful URL page recoveries must operate identically.
* **Browser Recovery Behavior**: Browser crash event listeners, process restarts, generation index increments, and self-healing context creations must execute correctly.
* **Generation Rollover Semantics**: Old generation purges must atomically invalidate the registry, immediately close `IDLE` tabs, and route `LEASED` tabs to background orphan cleanups to prevent stalled resource leaks.

---

### Decision 6: Incremental Migration Phases

The migration is structured into six highly conservative phases to optimize for rollback safety and clear verification bounds:

#### Phase 1: Introduce `BaseProviderAdapter` and Script Decoupling
- **Action**: Create `base_adapter.py`. Create `adapters/scripts/gemini_scripts.py` and move the JS scripts (`STREAM_EXTRACTOR_SCRIPT`, `STOP_OBSERVER_SCRIPT`) out of the providers directory.
- **Rollback Safety**: Low risk. Old scripts are imported directly from the new module. No behavioral change.
- **Verification**: Run service tests. Verify that JS scripts load and execute identically.

#### Phase 2: Extract Gemini DOM/Auth Logic into `GeminiProviderAdapter`
- **Action**: Create `adapters/gemini_adapter.py` implementing `BaseProviderAdapter`. Port prompt submission, URL parsing, and auth verification out of `GeminiPlaywrightProvider` and `BrowserEngine`.
- **Rollback Safety**: Keep a git checkpoint. The adapter can be hot-swapped back if any selector visibility or click sequence is missed.
- **Verification**: Run completions. Verify prompt filling, sending, and completion extraction works on the Gemini interface.

#### Phase 3: Decouple `tab.py` (Tab Models and Request Leases)
- **Action**: Create `tab.py`. Extract `TabStatus`, `PersistentTab`, and `ManagedPage` from `engine.py`. Update all imports.
- **Rollback Safety**: Minimal logical changes. Only code relocation.
- **Verification**: Verify that the private locks and shielded `_do_close` logic continue to execute cleanly.

#### Phase 4: Decouple `session.py` (Registry, Sweeper Tasks)
- **Action**: Create `session.py`. Move `ProviderSession` and its four loops (`_reaper_loop`, `_eviction_loop`, `_autosave_loop`, and the orphan sweep task) from `engine.py`.
- **Rollback Safety**: Check that no awaits have slipped inside `registry_lock` during import/code migration.
- **Verification**: Run continuous tests for 15+ minutes to verify idle timeout eviction, active reaper probing, and browser state saving execute correctly.

#### Phase 5: Reduce `BrowserEngine` to Orchestration Only
- **Action**: Modify `engine.py` to only contain `BrowserEngine` and its process/soft-cap logic. Import `ProviderSession` from `session.py` and use the decoupled structures.
- **Rollback Safety**: Clean git diff comparisons of process lifecycle methods.
- **Verification**: Induce a browser crash and verify that `BrowserEngine` initiates generation rollover, purges all old tabs, and spawns new sessions cleanly.

#### Phase 6: Prepare the Multi-Provider Registry System
- **Action**: Introduce a simple lazy session mapper mapping providers to their adapter instance.
- **Verification**: Run existing tests across all modes to ensure absolute regression-free operation.
