## Why

The current gateway architecture suffers from logic leakage into `chat.py` and operational fragility in configuration persistence. To support a growing list of heterogeneous AI providers, we must stabilize the gateway layer while maintaining "provider-owned complexity." This ensures that the gateway remains a thin orchestrator while providers handle their own asymmetric implementation details.

## What Changes

- **Operational Safety Fixes (Phase 0)**: Resolve race conditions in cookie/config persistence and eliminate blocking I/O in the configuration rotation path.
- **Lightweight Provider Contract**: Establish a minimal interface for providers to handle their own request mapping and response normalization without forcing internal symmetry.
- **SSE Boundary Normalization**: Standardize response streaming only at the OpenAI/SSE boundary, allowing providers to maintain their own internal streaming implementations.
- **Pragmatic Gateway Decomposition**: Refactor `chat.py` into a thin router that delegates to provider-specific modules after operational safety is ensured.

## Capabilities

### New Capabilities
- `operational-safety`: Non-blocking, concurrency-safe configuration and cookie persistence.
- `lightweight-provider-interface`: A minimal contract focused on external behavior normalization.
- `sse-boundary-utility`: Shared utility for formatting final OpenAI-compatible SSE chunks.

### Modified Capabilities
- `gemini-integration`: Localize Gemini's web-session and prompt-emulation complexity within the provider.
- `atlas-integration`: Adhere to the lightweight contract while maintaining its stateless, HTTP-native implementation.

## Impact

- `src/app/config.py` & `src/models/gemini.py`: Critical updates for concurrency-safe persistence.
- `src/app/endpoints/chat.py`: Incremental refactoring into a thin orchestrator.
- Providers: Encapsulated lifecycle, retries, and internal streaming logic.
- Backward compatibility: Fully preserved for all OpenAI-compatible clients.
