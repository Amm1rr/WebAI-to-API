## MODIFIED Requirements

### Requirement: Authentication Failure Classification
The system SHALL classify authentication checks and startup status failures into two distinct types based entirely on DOM and navigation state:
1. **Transient Failures**: Playwright locator timeouts, temporary DOM loading delays, network socket errors, or temporary navigation glitches occurring before prompt submission.
2. **Hard Failures**: A confirmed, stable redirect to the Google accounts sign-in UI or a stable, visible "sign in" button that remains present after the grace period.
The `GeminiProviderAdapter.check_authentication` method MUST remain a lightweight, stateless, and fast DOM inspector with no internal sleep, polling, or retry loops. Transient failures MUST NOT trigger permanent session storage state (`gemini.json`) deletion. Hard failures SHALL immediately trigger an authentication status cache update to `EXPIRED_SESSION` and propagate the failure to block redundant browser creation on subsequent requests until manual or on-demand login completes.

#### Scenario: Transient Network Lag During Check
- **WHEN** the authentication check encounters a Playwright timeout or a network socket disconnect during page status loading
- **THEN** the system SHALL treat this as a transient failure, preserve the persistent `gemini.json` state file, and allow a pre-submission retry

#### Scenario: Explicit Invalid Session Verified
- **WHEN** the page redirects stably to the Google accounts sign-in screen and the sign-in button is confirmed visible after the grace period
- **THEN** the system SHALL classify this as a hard failure, raise a terminal exception, trigger an auth status refresh to `EXPIRED_SESSION`, and log the state as unauthenticated
