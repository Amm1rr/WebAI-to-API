# conversation-concurrency-limits Specification

## Purpose
TBD - created by archiving change harden-conversation-ownership-concurrency. Update Purpose after archive.
## Requirements
### Requirement: Dedicated Concurrency Coordination Lock
The system MUST implement a dedicated, isolated lock named `conversation_lock` exclusively for conversation ownership coordination. The `conversation_lock` MUST protect ONLY `active_conversations` and MUST be completely decoupled from the tab `registry_lock`.

#### Scenario: Verify lock separation and isolation
- **WHEN** conversation ownership reservation, rollback, or conditional release is performed
- **THEN** the system SHALL acquire `conversation_lock` to serialize the operations
- **AND** the system SHALL NOT acquire `registry_lock` while holding `conversation_lock`, nor acquire `conversation_lock` while holding `registry_lock`

### Requirement: Atomic Ownership Reservation
The system MUST implement a distinct ownership reservation phase that executes atomically under `conversation_lock` before any lease acquisition or Playwright operations begin. The "busy check" and "ownership reservation" MUST be a single indivisible critical section.

#### Scenario: Successful ownership reservation
- **WHEN** a request targets a `conversation_id` and the conversation is not registered in `active_conversations`
- **THEN** the system SHALL immediately reserve the conversation by mapping `active_conversations[conversation_id] = request_id` under `conversation_lock` in a single step before any asynchronous yields

### Requirement: Exclusive Single-Source Concurrency Verification
The `active_conversations` mapping SHALL be the ONLY authoritative source of concurrency state. Competing concurrent requests targeting a reserved conversation MUST fail-fast immediately. The system SHALL NOT use secondary states (such as tab status, registry contents, or tab lease status) to determine if a conversation is busy.

#### Scenario: Reject concurrent request immediately
- **WHEN** a request targets a `conversation_id` that is already reserved in `active_conversations`
- **THEN** the system SHALL reject the request immediately with a HTTP 409 Conflict error (or a semantic `ConversationBusyError`), without internal queueing, waiting, or retrying

### Requirement: Guarded Reservation Rollback on Failure
Successful lease acquisition is a separate lifecycle stage from ownership reservation. If lease acquisition or page setup fails after reservation, the system MUST execute a guarded rollback under `conversation_lock` to clear the reservation ONLY if the current request is still the registered owner of the reservation.

#### Scenario: Rollback failed acquisition safely
- **WHEN** lease acquisition fails after reservation has occurred
- **AND** `active_conversations[conversation_id]` still matches the current request's ID
- **THEN** the system SHALL remove the reservation mapping from `active_conversations` under `conversation_lock` so the conversation is immediately available for other requests

### Requirement: Conditional Lease Release
Upon request completion, ownership release MUST be conditional. A releasing request is permitted to remove ownership under `conversation_lock` ONLY if it is verified to be the current authoritative owner of the conversation.

#### Scenario: Conditional release on request termination
- **WHEN** a request terminates successfully or via client cancellation
- **THEN** the system SHALL verify that `active_conversations` still maps the `conversation_id` to this request's ID under `conversation_lock` before removing it

### Requirement: Stale-Finalizer Protection
Stale cleanup and finalization paths are non-authoritative. Stale cleanup paths MUST silently abort any ownership mutation attempts under `conversation_lock` to prevent them from clearing or mutating newer active ownership.

#### Scenario: Guard ownership mutation against stale cleanup
- **WHEN** a stale cleanup path from an older request attempts to clear or mutate ownership for a `conversation_id`
- **THEN** the system SHALL detect under `conversation_lock` that the current registered owner is a newer request and SHALL silently abort the mutation without throwing errors or clearing the new ownership

