# error-and-lifecycle-hardening Specification

## Purpose
TBD - created by archiving change harden-error-handling-and-lifecycle. Update Purpose after archive.
## Requirements
### Requirement: Reaper Loop Rollover Safe State Handling
The ProviderSession reaper loop MUST NOT trigger engine-scoped terminal shutdown upon detecting a browser generation rollover mismatch. The reaper loop MUST ignore the stale generation state, MUST NOT trigger engine shutdown, and MUST yield recovery responsibility exclusively to the authoritative session recovery flow.

#### Scenario: Generation Rollover Detected
- **WHEN** the reaper loop executes and detects that last_browser_generation does not match engine.browser_generation
- **THEN** the loop SHALL skip active liveness validation, skip calling the browser disconnected callback, and delegate subsequent recovery handling to the authoritative session recovery flow

### Requirement: Queue Overflow Request Termination
Queue saturation is a terminal request-scoped failure. Queue saturation MUST terminate the active request deterministically, MUST invalidate the active request stream state, and the callback MUST NOT silently drop events. Whether the underlying tab or page becomes poisoned is implementation-dependent and determined by runtime integrity checks.

#### Scenario: Event Queue Saturation
- **WHEN** the event buffer reaches saturation limit during enqueuing
- **THEN** the request stream SHALL deterministically transition to a failed state and terminate, while leaving broader session liveness to be validated by runtime integrity checks

### Requirement: Cancellation-Safe Resource Teardown
All resource release operations (such as locks, semaphore permits, and callback registries) inside ManagedPage teardown MUST prioritize deterministic cleanup, including lock acquisition waits and resource release operations. Teardown MUST prioritize semaphore and lease release, strongly securing their cleanup under normal task cancellation, while auxiliary Playwright cleanup remains best-effort.

#### Scenario: Request Cancelled During Teardown Lock Entry
- **WHEN** the task is cancelled while ManagedPage teardown is waiting to acquire its locks
- **THEN** the release logic SHALL proceed to ensure that the session semaphore permit is returned and the persistent tab lease is released

### Requirement: Mutually Exclusive Tab Status and Closure Mutations
All modifications to PersistentTab status and page-level closures MUST be serialized through authoritative locking. Un-synchronized status transitions or concurrent closures are strictly forbidden.

#### Scenario: Concurrent Tab Closure and Lease Release
- **WHEN** tab closure is called concurrently with lease release
- **THEN** both operations SHALL be serialized by the tab's internal lock, preventing concurrent page close attempts and ensuring a deterministic DEAD status transition

### Requirement: Restrict Recovery Execution to ProviderSession
Providers detect and escalate failures, but MUST NOT directly manage storage state file deletions or execute context setups on authentication failure. Providers MUST raise a recovery-scoped exception to escalate the condition, leaving ProviderSession as the sole recovery executor.

#### Scenario: Authentication Failure Recovery Escalation
- **WHEN** the provider detects authentication loss on a page
- **THEN** it SHALL raise a recovery-scoped exception and terminate the request, allowing the ProviderSession to catch the error, invalidate the context, and manage state recreation on subsequent requests

