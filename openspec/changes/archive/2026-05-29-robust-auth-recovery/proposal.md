## Why

The current Playwright-based Gemini session authentication check and recovery process lacks granularity. When the adapter performs the authentication check, any failure—whether it is a transient network timeout, temporary Google page redirect, slow rendering, or actual cookie expiration—is treated as a fatal authentication failure. This escalates immediately to a destructive session recovery path that permanently deletes the user's valid `gemini.json` session file. Consequently, temporary network glitches or transient loading states result in permanent session eviction, requiring manual bootstrap re-authentication by the user. There is a need to distinguish between transient failures and hard expiration, ensuring that persistent auth state is never accidentally destroyed.

## What Changes

- Introduce a dual-classification model for authentication failures based strictly on DOM and browser navigation state: **Transient Failures** (e.g., timeouts, network issues, temporary DOM loading states during startup) and **Hard Failures** (e.g., stable redirects to Google account sign-in UI, stable visible sign-in button).
- Guarantee that the persistent `gemini.json` state file is **never** deleted or purged upon encountering transient authentication or network errors.
- Implement an exponential backoff retry policy strictly limited to a **very narrow pre-submission boundary** (page acquisition, page readiness, authentication verification, and observer preparation). Once prompt filling, submit clicking, or streaming starts, no automatic retries SHALL occur.
- Keep the `GeminiProviderAdapter.check_authentication()` method a lightweight, stateless, side-effect-free DOM inspector. The 2.5-second redirect grace-period logic MUST be orchestrated as a bounded outer flow in the provider/request layer, not inside the adapter.
- Ensure that failed pre-submission attempts **fully release page leases and resources** before initiating a retry, preventing stale lease holding and lock contention.
- Keep the entire architecture DOM and navigation-based only, strictly avoiding any cookie, storage-state, or network response-layer introspection.

## Capabilities

### New Capabilities
- `robust-auth-recovery`: Implements DOM/navigation-based failure classification, a highly bounded pre-submission retry policy, a stateless check adapter, an outer redirect grace-period flow, and lease-safe recovery triggers.

### Modified Capabilities
- `error-and-lifecycle-hardening`: Integrates transient-resilient recovery triggers and prevents destructive state deletion within the session failure handling flow.

## Impact

- **Affected Code**: `src/app/services/browser/session.py` (escalation and recovery flow), `src/app/services/browser/adapters/gemini_adapter.py` (lightweight DOM-based authentication check), `src/app/services/providers/gemini_playwright.py` (pre-submission retries, lease releasing, and redirect grace-period orchestration).
- **APIs**: No public API changes. Error responses will only be returned after pre-submission retries are exhausted or when a hard authentication failure is definitively confirmed.
- **Dependencies**: Depends entirely on Playwright's Page/Browser API and the existing Uvicorn/FastAPI async context.
