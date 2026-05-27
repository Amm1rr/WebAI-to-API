## Context

The gateway layer currently carries too much provider-specific weight, particularly in `chat.py`. Additionally, the mechanism for persisting rotated cookies is prone to race conditions and uses blocking I/O, which threatens the stability of the entire service under load.

## Goals / Non-Goals

**Goals:**
- Prioritize operational safety (concurrency-safe config persistence).
- Maintain "Provider-Owned Complexity": Each provider handles its own quirks (sessions, retries, internal streaming).
- Establish an extremely lightweight contract for external behavior normalization.
- Normalize streaming ONLY at the OpenAI/SSE boundary.

**Non-Goals:**
- No heavy inheritance or enterprise abstraction layers.
- No forced symmetry between browser-based and HTTP-native providers.
- No dynamic plugin loading; keep the registry simple and static.

## Decisions

### 1. Concurrency-Safe Config Persistence (Phase 0)
- **Decision:** Use atomic file writes and non-blocking I/O for `config.conf` updates.
- **Rationale:** Prevents corruption during concurrent cookie rotation events. This is the highest priority stability fix.

### 2. Extremely Lightweight Provider Contract
- **Decision:** A simple async interface focused on input (OpenAI Request) and output (OpenAI Response/Stream).
- **Rationale:** Avoids forcing providers into a rigid internal structure. Providers remain responsible for their own lifecycle and transformation logic.

### 3. Asymmetric Streaming Normalization
- **Decision:** Normalization happens only when converting provider outputs to SSE `data:` chunks for the client.
- **Rationale:** Allows Atlas to use its native `httpx` streaming while Gemini uses its simulated/buffered streaming internally. The gateway only cares about the final OpenAI-compatible delivery.

### 4. Static Provider Registry
- **Decision:** A hardcoded mapping in `src/app/services/factory.py`.
- **Rationale:** Simple, traceable, and sufficient for the current scale. Avoids the complexity of dynamic loading.

## Risks / Trade-offs

- **[Risk]** Lack of internal symmetry might make the gateway orchestrator slightly more complex. → **[Mitigation]** The orchestrator only interacts with the high-level `chat_completions` method; provider-specific complexity remains internal to the provider modules.
- **[Risk]** Atomic file writes might still be slow on some filesystems. → **[Mitigation]** Ensure the write operations are truly offloaded from the main event loop to avoid blocking.
