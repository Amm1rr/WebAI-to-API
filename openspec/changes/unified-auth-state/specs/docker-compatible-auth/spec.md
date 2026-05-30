## MODIFIED Requirements

### Requirement: Session State Persistence Boundaries
The system SHALL enforce the guarantee that no automatic persistent storage state rewrites occur during normal request execution. The runtime API service is permitted to mutate in-memory browser contexts and client states during request execution, but it SHALL NOT automatically overwrite or mutate the persistent state files on disk, nor SHALL it ever write to `config.conf` to store authentication cookies during active API requests. The on-demand login bootstrap service and the manual bootstrap utility SHALL act as the exclusive writers of the persistent state files. Any runtime-triggered state saving SHALL be executed only inside explicit, isolated login workflows or manual bootstrap, and normal request execution paths MUST NOT execute background or active persistence tasks.

#### Scenario: In-memory context mutation without persistent rewrite
- **WHEN** a client completions request is processed and performs in-memory modifications (such as page creation or session cookie adjustments in browser memory)
- **THEN** the runtime API service SHALL NOT execute any file-level write, save, or overwrite operations on the persistent state files in `auth_state_dir`
- **AND** the filesystem-backed state file SHALL remain unmodified upon request completion or cancellation

#### Scenario: Shared Session Infrastructure Layer Ownership
- **WHEN** a state file is loaded or saved
- **THEN** it SHALL be executed exclusively by the shared session infrastructure layer
- **AND** the provider adapters SHALL NOT directly perform state loading, write operations, or file mutations

#### Scenario: Safe atomic state serialization
- **WHEN** the manual bootstrap utility or shared session infrastructure layer writes the session state to disk
- **THEN** it SHALL write the JSON payload to `{provider}.json.tmp` inside the `auth_state_dir`
- **AND** it SHALL execute a physical `fsync` on the file descriptor
- **AND** it SHALL atomically replace the target `{provider}.json` file using an atomic rename operation

## ADDED Requirements

### Requirement: Priority Auth Loading and Legacy Fallback
The system SHALL support a prioritized authentication loading hierarchy that guarantees backward compatibility for legacy installations. When initializing a Gemini provider pathway, the system SHALL first attempt to load cookies from the canonical state file `runtime/auth/gemini.json`. If missing or invalid, the system SHALL fallback to load read-only from the legacy `[Cookies]` section of `config.conf` and log an official deprecation warning, while exposing a migration-needed status flag.

#### Scenario: Primary loading from canonical state file
- **WHEN** a Gemini session boots and a valid `runtime/auth/gemini.json` file is present
- **THEN** the system SHALL load the authentication state from this file
- **AND** it SHALL bypass any fallback check of legacy config sections

#### Scenario: Fallback to legacy configuration with migration warning
- **WHEN** a Gemini session boots and `runtime/auth/gemini.json` is missing but legacy `[Cookies]` are configured inside `config.conf`
- **THEN** the system SHALL load the legacy cookies read-only, boot the session successfully, log a clear deprecation warning
- **AND** it SHALL expose the `migration_needed: true` status flag in the auth status payload
- **AND** the system SHALL NOT write to the filesystem or trigger automatic background state file serialization

## REMOVED Requirements

### Requirement: Runtime Configuration-Backed Cookie Persistence
**Reason**: Storing active authentication state in `config.conf` couples application configuration with dynamic auth tokens and violates file immutability boundaries.
**Migration**: Transition all cookie reads and writes to `runtime/auth/gemini.json`. Read legacy config cookies solely as a deprecated fallback.
