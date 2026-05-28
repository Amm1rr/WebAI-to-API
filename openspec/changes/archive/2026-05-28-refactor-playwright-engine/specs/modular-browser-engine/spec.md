## ADDED Requirements

### Requirement: Minimal Provider Adapter Interface
The system SHALL define a minimal `BaseProviderAdapter` interface that abstracts only vendor-specific authentication checks, URL state parsers, and prompt DOM submission sequences.

#### Scenario: Non-behavioral adapter execution
- **WHEN** the system executes an authentication check, extracts a conversation ID, or submits a prompt
- **THEN** it SHALL delegate that operation directly to the registered `BaseProviderAdapter` instance.
- **AND** the adapter SHALL NOT modify stream pipelines, serialization locks, or process orchestration pathways.

### Requirement: Decoupled Tab Lifecycle Management
The system SHALL manage browser tab lifecycles, states, and locking rules in a dedicated `tab` module, completely separated from process management and DOM-interaction logic.

#### Scenario: Shielded lease release
- **WHEN** a request completes or is cancelled
- **THEN** the `ManagedPage` SHALL execute its release logic inside an `asyncio.shield` block to return the semaphore slot to the session and return or close the `PersistentTab` resource.

### Requirement: Isolated Provider Session Registry
The system SHALL isolate the active tab registry, background sweep loops, and concurrency boundaries for each provider session in a dedicated `session` module.

#### Scenario: Background sweeper execution
- **WHEN** the session is initialized
- **THEN** it SHALL start `_reaper_loop`, `_eviction_loop`, and `_autosave_loop` tasks that run concurrently, catch internal errors to prevent process crashes, and interact with tab locking structures safely.
