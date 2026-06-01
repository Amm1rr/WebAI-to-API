# Streaming Pipeline

This document specifies the end-to-end event flow, normalization, and synchronization logic for the browser-based streaming pipeline.

## 1. Event Flow Architecture

The pipeline bridges the asynchronous gap between browser-side DOM events and the server-side HTTP stream.

### 1.1 Bridge Lifecycle
1. **Exposure**: A permanent binding (`__gemini_bridge`) is exposed on the page.
2. **Registration**: Each request registers a unique callback in `page._gemini_callbacks` using its `request_id`.
3. **Dispatch**: The browser-side script calls the bridge with a payload containing the `request_id` and event `type`.

### 1.2 Event Types
- `ready`: Browser-side observer is initialized and ready.
- `started`: Generation has officially begun (authoritative).
- `chunk`: A new text delta is available.
- `rewrite`: **Authoritative full-text replacement**. Used when the browser-side renderer flickers or updates a previously emitted block.
- `done`: Generation complete.

## 2. Synchronization Invariants

### 2.1 Authoritative Confirmation
- **Mechanism**: An `asyncio.Event` named `submission_confirmed`.
- **Logic**: The event is set only when the bridge emits `started`, `chunk`, `rewrite`, or `done`. 
- **Purpose**: Prevents the HTTP request from timing out or returning before the browser has actually accepted the prompt.

### 2.2 Lazy Conversation Registration
- **Detection**: The `conversation_id` is extracted from the browser URL during generation.
- **Registration**: Once a URL change is detected, the temporary lease is promoted to a `PersistentTab` in the registry.

## 3. Rewrite-Resilient Streaming

The pipeline is designed to handle providers that "rewrite" the full response text during generation.
- **Incremental Emission**: To prevent duplicate SSE output, the streaming generator must maintain a `last_emitted_text` snapshot.
- **Rewrite Semantics**: Upon receiving a `rewrite` event with `full_text`, the generator must calculate the suffix relative to `last_emitted_text` and emit only the new incremental content.
- **Ordering Guarantee**: A `rewrite` event supersedes all previously emitted text state for that request stream.

## 4. Callback Registry Lifecycle

### 4.1 Ownership & Cleanup
- **Registry**: `page._gemini_callbacks` is a shared registry for all concurrent requests on a single page.
- **Mandatory Cleanup**: Every registered callback MUST be removed from the registry in a `finally` block after stream completion, cancellation, or failure.
- **Risk**: Stale callback entries are considered a memory leak and a cross-request contamination risk.

## 5. Backpressure & Memory Safety

### 5.1 Playwright to SSE Generator
- **Queue Boundary**: A request-scoped `asyncio.Queue` acts as the buffer between the Playwright bridge callback and the HTTP SSE generator.
- **Capacity**: The queue is **bounded** (typically `maxsize=100`) to prevent unbounded memory growth.
- **Overflow Contract**: Queue saturation is considered a **terminal request-stream failure**. 
    - **No Silent Drops**: Bridge callbacks MUST NEVER silently drop `chunk` or `rewrite` events, as this corrupts the stream state.
    - **Fatal Termination**: If enqueue fails, the request stream must transition into a failed state and terminate immediately.
    - **Isolation**: The affected `request_id` is invalidated, but the broader `ProviderSession` must remain healthy.

### 5.2 Non-Blocking Callbacks
- **Invariant**: Bridge callbacks MUST NEVER perform network I/O or any blocking work. They must enqueue payloads and return immediately to allow Playwright's event dispatch to progress.
- **Client Isolation**: Slow HTTP clients must not stall Playwright event dispatching. The generator consumes from the queue independently.

## 6. Event Ordering Guarantees

- **Sequence**: Events for a single `request_id` must be processed strictly in arrival order (`started` -> `chunk`/`rewrite` -> `done`).
- **Corruption**: Out-of-order event handling is considered stream corruption and must be prevented by the ordered queue boundary.

## 7. Normalization & Termination

### 7.1 SSE Pipeline
- **Format**: All events are normalized to OpenAI-compatible Server-Sent Events (SSE).
- **Finalization**: Every successful stream MUST terminate with a literal `data: [DONE]` chunk.

### 7.2 Failure Isolation & Propagation
- **Isolation**: Failures in one request stream (e.g., generator error or client disconnect) must not terminate or poison the `ProviderSession` unless the underlying page/context becomes invalid.
- **Timeout**: If no chunks are received within `chunk_timeout`, the stream is terminated with a `TimeoutError`.
- **Cancellation**: If the client disconnects, the provider MUST attempt to click the "Stop" button in the browser UI before cleaning up.

## 8. AI Agent Rules

AI Agents working on the streaming pipeline must adhere to these strict constraints:

1. **Zero Duplication**: Never emit duplicate text or overlapping SSE chunks after a `rewrite` event.
2. **Immediate Return**: Never perform blocking work or I/O inside Playwright bridge callbacks.
3. **Leak Prevention**: Never leave a `request_id` in the callback registry after a stream terminates.
4. **Data Integrity**: Never trade stream correctness for silent event dropping in ordered streaming pipelines.
5. **Memory Safety**: Always use bounded queues for cross-boundary event passing.
6. **Ordered Dispatch**: Preserve strict FIFO event ordering for every request stream.
