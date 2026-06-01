# WebAI-to-API Backlog Investigation: Hot Reload Playwright Authentication State

## Context

Current Playwright authentication flow:

Host:

* `poetry run python verify_login.py`
* Creates `runtime/auth/gemini.json`

Docker:

* `./runtime:/app/runtime`
* Container reads `/app/runtime/auth/gemini.json`
* Authentication state is loaded into Playwright `storage_state`

Current behavior:

* `gemini.json` is read during Playwright context creation (`_setup()`).
* Active contexts do NOT monitor the file for changes.
* Updating `runtime/auth/gemini.json` does NOT affect existing Playwright contexts.
* New auth state is picked up only when a new context is created.
* Container restart currently acts as the practical mechanism to force auth reload.

Verified code path:

`gemini.json`
→ `load_canonical_state()`
→ `GeminiAuthSelector.first_playwright_storage_candidate()`
→ `translate_to_playwright()`
→ `browser.new_context(storage_state=...)`

Current user workflow:

1. Run `poetry run python verify_login.py`
2. Update `runtime/auth/gemini.json`
3. Restart container
4. New auth state becomes active

## Investigation Goal

Determine whether WebAI-to-API should support reloading Playwright authentication state without requiring a container restart.

## Questions To Answer

### 1. Feasibility

Can authentication state be reloaded safely by:

* Recreating only the Playwright context?
* Recreating the provider session?
* Rolling browser generation?
* Forcing runtime recovery?
* Introducing a dedicated auth reload operation?

Trace all relevant lifecycle code.

### 2. Architecture Impact

Evaluate impact on:

* BrowserEngine
* ProviderSession lifecycle
* Tab registry
* Leases
* Active requests
* Keepalive pages
* Session recovery
* Generation rollover
* Concurrency model
* Lifecycle and recovery guarantees

Determine whether auth reload violates existing architectural invariants.

### 3. Safety Analysis

Determine:

* What happens if requests are active during auth reload?
* Can tabs become orphaned?
* Can leases leak?
* Can session ownership be broken?
* Can browser generations become inconsistent?

### 4. Possible Designs

Compare:

A. Full container restart (current)

B. Browser generation rollover

C. Provider session reset

D. Dedicated `/v1/auth/reload` endpoint

E. File watcher on `runtime/auth/gemini.json`

For each option:

* Complexity
* Risk
* Operational benefit
* Architectural compatibility

### 5. Over-Engineering Assessment

Determine whether auth hot reload is:

* Production-worthy
* Nice-to-have
* Rarely needed
* Over-engineering

Support conclusions with code evidence and operational reasoning.

## Deliverable

Produce a design audit, not an implementation.

Do NOT modify code.

Do NOT implement anything.

First determine:

1. Whether hot auth reload is worth building.
2. What the safest architecture would be.
3. Whether the operational value justifies the added complexity.
