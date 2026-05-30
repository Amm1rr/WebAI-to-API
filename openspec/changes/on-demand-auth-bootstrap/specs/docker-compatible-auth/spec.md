## MODIFIED Requirements

### Requirement: Session State Persistence Boundaries
The system SHALL enforce the guarantee that no automatic persistent Playwright storage state rewrites occur during normal request execution. The runtime API service is permitted to mutate in-memory Playwright browser contexts during request execution, but it SHALL NOT automatically overwrite or mutate the persistent state files on disk during active API requests (such as `/v1/chat/completions`). The on-demand login service and the manual bootstrap utility SHALL act as the exclusive writers of the persistent state files. Any runtime-triggered state saving SHALL be executed only inside explicit, isolated login workflows or manual bootstrap, and normal request execution paths MUST NOT execute background or active persistence tasks.

#### Scenario: In-memory context mutation without persistent rewrite
- **WHEN** a client completions request is processed and performs in-memory modifications (such as page creation or session cookie adjustments in browser memory)
- **THEN** the runtime API service SHALL NOT execute any file-level write, save, or overwrite operations on the persistent state files in `auth_state_dir`
- **AND** the filesystem-backed state file SHALL remain unmodified upon request completion or cancellation

#### Scenario: Shared Session Infrastructure Layer Ownership
- **WHEN** a state file is loaded or saved
- **THEN** it SHALL be executed exclusively by the shared session infrastructure layer
- **AND** the provider adapters SHALL NOT directly perform state loading, write operations, or file mutations

#### Scenario: Safe atomic state serialization
- **WHEN** the manual bootstrap utility, on-demand login service, or shared session infrastructure layer writes the session state to disk
- **THEN** it SHALL write the JSON payload to `{provider}.json.tmp` inside the `auth_state_dir`
- **AND** it SHALL execute a physical `fsync` on the file descriptor
- **AND** it SHALL atomically replace the target `{provider}.json` file using an atomic rename operation
