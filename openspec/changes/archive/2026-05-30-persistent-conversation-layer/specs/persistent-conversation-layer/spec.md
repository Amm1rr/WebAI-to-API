## ADDED Requirements

### Requirement: Provider-Agnostic Conversation Snapshot Schema
The system SHALL define a provider-agnostic `ConversationSnapshot` schema that separates generic conversation properties (such as conversation identifier and timestamp) from provider-specific internal session states. The provider-specific state MUST be encapsulated under a dedicated `session_state` field.

#### Scenario: Verify provider-agnostic serialization structure
- **WHEN** a conversation snapshot is serialized for storage
- **THEN** the system SHALL produce a payload containing only `conversation_id` (primary key), `provider_name` (string), `session_state` (dictionary), `schema_version` (integer), and `updated_at` (timestamp)
- **AND** the generic schema SHALL NOT reference any provider-specific properties (such as Gemini's metadata, rid, gem_id, or context_str) directly outside `session_state`

### Requirement: Restart-Safe Session Recovery Lifecycle
The `SessionRegistry` SHALL implement a lazy-restoration recovery lifecycle. Upon receiving a request with an existing `conversation_id` after a process restart, the registry MUST retrieve the snapshot from the storage layer, recreate the provider session, and restore the exact session variables before executing the prompt.

#### Scenario: Recover session successfully from database snapshot
- **WHEN** a request arrives targeting an existing `conversation_id` that is not in active memory
- **THEN** the system SHALL load the corresponding `ConversationSnapshot` from the storage layer
- **AND** the system SHALL instantiate a new `SessionManager` and restore the provider's internal state using the serialized snapshot payload
- **AND** the system SHALL execute the new request as a resumed stateful conversation

### Requirement: Recovery Validation Hook Boundary (`validate_session_recovery`)
The provider adapter validation hook `validate_session_recovery(...)` MUST operate exclusively on the persisted snapshot state during lazy database restoration.

#### Scenario: Verify validate_session_recovery contract boundaries
- **WHEN** a database snapshot is restored
- **THEN** the system SHALL invoke the `validate_session_recovery(session_state, client_context)` hook
- **AND** the hook SHALL execute snapshot schema validation, state integrity verification, `provider_state_version` validation, and provider-specific restoration safety checks
- **AND** the hook SHALL NOT validate active cached sessions or perform cache-hit ownership checks

### Requirement: Recovery Validation and Fail-Closed Policy
The recovery lifecycle MUST validate snapshot integrity before resuming the conversation. If a snapshot is corrupted, invalid, missing, or fails provider-specific validation hooks, the system SHALL fail explicitly and raise a recovery error category (`SnapshotNotFoundError`, `StateIntegrityError`, or `ProviderThreadExpiredError`), rather than silently creating a new blank thread.

#### Scenario: Fail closed instead of creating a replacement conversation
- **WHEN** a requested `conversation_id` is not present in active memory and its persisted snapshot is missing, corrupted, invalid, or rejected by provider-specific recovery validation
- **THEN** the system SHALL raise the corresponding recovery error category
- **AND** the system SHALL NOT create a new blank replacement conversation for that requested `conversation_id`

### Requirement: DEFAULT_METADATA Shared-Reference Mitigation
The restoration flow MUST prevent global metadata corruption caused by the shared-reference mutation bug in `gemini-webapi`'s `DEFAULT_METADATA` list constant. The restoration flow MUST create an isolated metadata copy and break any shared reference to `DEFAULT_METADATA` before the session is used.

#### Scenario: Verify global metadata isolation during restoration
- **WHEN** a session is restored from a database snapshot
- **THEN** the system SHALL guarantee that the session's active metadata is isolated from `gemini_webapi.constants.DEFAULT_METADATA`
- **AND** subsequent updates to the session's metadata SHALL NOT mutate the shared global `DEFAULT_METADATA` list constant

### Requirement: Provider Capability Contract
Every provider adapter MUST explicitly declare its persistence and recovery capabilities through a scalable capability container model exposing standard tokens (such as `ProviderCapability.PERSISTENT_RECOVERY`). The `SessionRegistry` MUST dynamically orchestrate validation lifecycles based on these declared capabilities.

#### Scenario: Evaluate provider capabilities dynamically via container
- **WHEN** the `SessionRegistry` processes a request for a specific provider
- **THEN** the registry SHALL inspect the adapter's capability container (e.g. `capabilities: set[ProviderCapability]`)
- **AND** the registry SHALL NOT execute provider-name hardcoded branching checks (such as `if provider == "gemini"`)

### Requirement: Provider Session State Versioning
The opaque `session_state` JSON payload MUST contain a dedicated `provider_state_version` key representing the schema version of the provider's internal state. The provider adapter MUST validate this version at restoration time, and fail closed or execute local state migrations if the format is unsupported or outdated.

#### Scenario: Fail closed on incompatible provider state version
- **WHEN** a snapshot is lazy-loaded but its `session_state` contains an unsupported or corrupted `provider_state_version`
- **THEN** the system SHALL raise a `StateIntegrityError` and fail closed
- **AND** the system SHALL NOT restore the session or allow request execution

### Requirement: Decoupled Model Verification Policy
Model configuration and switching MUST be handled entirely within the provider adapter's logic. Recovery validation MUST NOT depend on model consistency. If the requested model differs from the model recorded in the snapshot metadata, recovery MUST proceed successfully, letting the provider adapter handle model switches internally.

#### Scenario: Resume conversation successfully with model mismatch
- **WHEN** a request to resume `conversation_id` specifies a model that differs from the model recorded in the snapshot metadata
- **THEN** the system SHALL successfully complete the recovery lifecycle
- **AND** the system SHALL NOT raise an integrity error or block execution due to the model mismatch

### Requirement: Synchronous Durable Snapshot Updates
The storage repository MUST persist snapshot updates synchronously and durably to the database. Updates to the persistent store SHALL execute before completing the request turn to guarantee crash-consistency and prevent client-server state split-brain divergence.

#### Scenario: Durable snapshot write on turn completion
- **WHEN** a stateful completion request completes and yields an updated session context from the provider
- **THEN** the system SHALL write the updated `ConversationSnapshot` synchronously and durably to the database in a single transaction before returning the response to the client
- **AND** the system SHALL update the in-memory `last_accessed` timestamp of the `SessionManager`
