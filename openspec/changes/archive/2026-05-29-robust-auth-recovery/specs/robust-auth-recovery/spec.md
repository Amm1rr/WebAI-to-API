## ADDED Requirements

### Requirement: Authentication Failure Classification
The system SHALL classify authentication checks and startup status failures into two distinct types based entirely on DOM and navigation state:
1. **Transient Failures**: Playwright locator timeouts, temporary DOM loading delays, network socket errors, or temporary navigation glitches occurring before prompt submission.
2. **Hard Failures**: A confirmed, stable redirect to the Google accounts sign-in UI or a stable, visible "sign in" button that remains present after the grace period.
The `GeminiProviderAdapter.check_authentication` method MUST remain a lightweight, stateless, and fast DOM inspector with no internal sleep, polling, or retry loops. Transient failures MUST NOT trigger permanent session storage state (`gemini.json`) deletion.

#### Scenario: Transient Network Lag During Check
- **WHEN** the authentication check encounters a Playwright timeout or a network socket disconnect during page status loading
- **THEN** the system SHALL treat this as a transient failure, preserve the persistent `gemini.json` state file, and allow a pre-submission retry

#### Scenario: Explicit Invalid Session Verified
- **WHEN** the page redirects stably to the Google accounts sign-in screen and the sign-in button is confirmed visible after the grace period
- **THEN** the system SHALL classify this as a hard failure, raise a terminal exception, and log the state as unauthenticated

---

### Requirement: Pre-Submission Retry Policy & Lease Preservation
The runtime execution layer MUST restrict the exponential backoff retry policy strictly to the **pre-submission phase** (page acquisition, page readiness checks, authentication verification, and observer preparation). 
Once prompt filling or submit actions begin (the **submission boundary**), the request becomes non-idempotent. Any exception after this boundary MUST bypass retry logic entirely and fail-fast immediately.
Furthermore, failed pre-submission attempts MUST **fully release page leases and resources** before initiating a retry, preventing stale lease holding and lock contention.

#### Scenario: Pre-Submission Failure Releases Lease Before Retry
- **WHEN** a transient failure occurs during page readiness or auth verification in the pre-submission phase
- **THEN** the system SHALL release the active `ManagedPage` lease and decrement the active lease count, wait for the backoff delay, and only then acquire a fresh lease for the next attempt

#### Scenario: Post-Submission Failure Bypasses Retry
- **WHEN** a page crash or timeout occurs during prompt filling, clicking submit, or active streaming
- **THEN** the system SHALL NOT attempt any retry, MUST release resources, and SHALL propagate the failure immediately to prevent duplicate submissions or inconsistent bridge state

---

### Requirement: Google Login Redirect Grace Period (Outer Orchestration)
The system MUST implement a 2.5-second grace period (polling every 500ms) to distinguish a momentary redirection during page loading from a terminal redirect to the Google accounts sign-in page. This polling grace-period loop MUST be executed as a bounded outer orchestration flow in the provider layer, not inside the adapter.

#### Scenario: Momentary Redirection Healed in Grace Period
- **WHEN** the page URL briefly redirects through a sign-in URL, but resolves back to the Gemini chat interface within 2.5 seconds
- **THEN** the system SHALL proceed with observer setup and prompt execution, treating the state as fully authenticated
