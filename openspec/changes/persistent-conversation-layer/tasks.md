## 1. Storage Layer and Snapshot Database Schema

- [ ] 1.1 Define the abstract `IConversationRepository` interface inside a new module `src/app/services/providers/base_repository.py`
- [ ] 1.2 Implement the `SQLiteConversationRepository` class with SQLAlchemy or standard sqlite3 connector inside `src/app/services/providers/sqlite_repository.py`
- [ ] 1.3 Create database initialization logic to setup `conversation_snapshots` table with minimalist generic columns: `conversation_id` (PK), `provider_name`, `session_state`, `schema_version`, and `updated_at`
- [ ] 1.4 Add basic CRUD unit tests to verify database creation, snapshot inserting, retrieving, and deleting

## 2. Provider State Serialization Adapters

- [ ] 2.1 Implement `serialize_session_state` and `deserialize_session_state` functions in `src/app/services/providers/gemini.py` to package `ChatSession` metadata context list, `gem_id`, `model_name`, and a `provider_state_version` integer key into the opaque `session_state` JSON string
- [ ] 2.2 Write isolated unit tests to assert that serializing and then deserializing a `ChatSession` state preserves exact metadata, Gem ID, and provider state versioning values
- [ ] 2.3 Add validation schema to verify schema version consistency during state deserialization

## 3. Session Registry Fail-Closed Restoration Flow

- [ ] 3.1 Update the `SessionRegistry` lookup flow in `src/app/services/session_manager.py` to query the storage repository when a requested `conversation_id` is an in-memory miss
- [ ] 3.2 Implement a restoration helper in `SessionRegistry` that instantiates a clean `SessionManager` and restores the internal `ChatSession` private state
- [ ] 3.3 Implement the provider validation hook `validate_session_recovery` (for lazy DB restoration checks, version validation, and snapshot integrity verification)
- [ ] 3.4 Define explicit provider capability contract flags via a capability container exposing standard tokens like `ProviderCapability.PERSISTENT_RECOVERY` on adapters
- [ ] 3.5 Update `SessionRegistry` to dynamically evaluate adapter capabilities without provider-type conditionals
- [ ] 3.6 Implement `provider_state_version` validation and migration checks inside the Gemini adapter restoration methods, raising `StateIntegrityError` on unsupported version formats
- [ ] 3.7 Implement copy isolation of restored metadata to break references to global `DEFAULT_METADATA` inside the restoration path, ensuring pristine global constants
- [ ] 3.8 Ensure recovery validation does NOT fail or block on model name mismatch, letting the provider adapter handle model switches internally

## 4. Atomic and Synchronous Durable Updates

- [ ] 4.1 Update `SessionRegistry` to coordinate synchronous and durable snapshot saving immediately upon successful request or stream completions, before returning response to client
- [ ] 4.2 Configure SQLite WAL-mode write transaction safety to ensure absolute crash-consistency and eliminate risk of client-server state split-brain divergence
- [ ] 4.3 Implement pruning synchronization ensuring that inactive DB snapshots are pruned after a configurable retention period (defaulting to 90 days of inactivity)

## 5. Integration Verification

- [ ] 5.1 Write an integration test recreating a process restart (instantiating the server, making a stateful chat completion, shutting down the server, starting a new server, and calling completions with the returned `conversation_id`)
- [ ] 5.2 Assert that `reused_conversation` is returned as `true` after the mock restart and that only the final message is sent to Google's backend
- [ ] 5.3 Write a validation test for the `DEFAULT_METADATA` shared-reference isolation workaround, asserting that a restored session's completions do not corrupt the global blank metadata list
