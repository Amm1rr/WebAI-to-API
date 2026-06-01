# Observability Hardening Verification

## Existing Request Correlation

**Status:** INSUFFICIENT

### HTTP Layer
- ❌ No request ID generation in endpoints
- ❌ No middleware for request correlation
- ❌ No X-Request-ID response header
- ❌ No access to request.state.request_id

### Provider Layer
- ✅ request_id generated in `playwright_adapter.py:80`
- ✅ request_id used in internal logging (lines 116, 122)
- ❌ request_id NOT propagated from HTTP layer
- ❌ No correlation between HTTP request and internal logs

### Browser Runtime
- ✅ request_id available in some tab logs
- ❌ No consistent propagation path

## Existing Stream Logging

**Status:** INSUFFICIENT

### Stream Lifecycle Events
- ❌ No stream start logging
- ❌ No stream completion logging
- ❌ No stream cancellation logging (with reason)
- ❌ No unexpected stream failure logging

### Currently Logged
- ✅ Page close warnings (line 116)
- ✅ Page crash warnings (line 122)
- ✅ Bridge emit failures (with stack trace)

## Existing Queue Overflow Visibility

**Status:** SILENT FAILURE

### Internal State Tracking
- ✅ `queue_overflow` boolean (line 68)
- ✅ `dropped_chunks` counter (line 57)
- ✅ State updated in bridge (lines 166-167)

### Logging
- ❌ queue_overflow NEVER logged
- ❌ dropped_chunks NEVER logged
- ❌ No overflow event visibility

**Impact:** Silent data loss in production

## Proposed Minimal Changes

### 1. Request ID Middleware (CRITICAL)
- Add middleware to generate request_id
- Store in request.state.request_id
- Return in X-Request-ID response header
- Make available to endpoints

### 2. Stream Lifecycle Logging (HIGH)
- Add stream start log with request_id
- Add stream completion log with duration
- Add stream cancellation log with reason

### 3. Queue Overflow Visibility (CRITICAL)
- Log queue overflow events
- Log dropped chunk counts
- Include request correlation

### 4. Error Correlation (HIGH)
- Ensure request_id visible in error responses
- Add minimal correlation support

## Implementation Scope

**DO NOT add:**
- Prometheus, Grafana, OpenTelemetry
- Metrics exporters
- Distributed tracing
- Logging architecture redesign

**DO add:**
- Request ID middleware
- Stream lifecycle logs
- Queue overflow logging
- Error correlation IDs
