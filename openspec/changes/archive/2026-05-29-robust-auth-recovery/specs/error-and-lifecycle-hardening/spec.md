## MODIFIED Requirements

### Requirement: Restrict Recovery Execution to ProviderSession
Providers detect and escalate failures, but MUST NOT directly manage storage state file deletions or execute context setups on authentication failure. Providers MUST raise a recovery-scoped exception to escalate the condition, leaving ProviderSession as the sole recovery executor. The ProviderSession MUST execute recovery by clearing in-memory contexts, resetting tab leases, and clearing runtime generation state, but MUST NOT delete or purge the persistent `gemini.json` storage state file from disk unless a hard, non-transient authentication expiration is explicitly verified.

#### Scenario: Authentication Failure Recovery Escalation
- **WHEN** the provider detects authentication loss on a page
- **THEN** it SHALL raise a recovery-scoped exception and terminate the request, allowing the ProviderSession to catch the error, invalidate the context, and safely attempt context recreation in subsequent requests without deleting the underlying persistent `gemini.json` storage state file
