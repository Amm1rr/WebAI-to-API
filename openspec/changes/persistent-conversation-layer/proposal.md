## Why

The current Gemini conversation persistence model stores conversation continuity state (`SessionManager` and `ChatSession` instances) purely in memory within the `SessionRegistry`. Upon process restarts, container recycling, or cache eviction/pruning under memory pressure, all active session managers are destroyed. While a history concatenation self-healing mechanism exists, a dedicated persistent conversation layer is required to store lightweight, provider-agnostic session snapshots, enabling true restart-safe session recovery without the overhead of history replay, while fully preserving backend-level conversation continuity under a strict fail-closed paradigm.

## What Changes

- **Opaque OID Mapping**: Establish an explicit mapping between the API client's opaque `conversation_id` and the provider-specific session snapshots.
- **Minimalist Provider-Agnostic Schema**: Introduce a generic `ConversationSnapshot` schema and storage abstraction. Generic database columns are stripped of all provider-specific concepts. The generic table is minimized strictly to `conversation_id` (primary key), `provider_name`, `session_state`, `schema_version`, and `updated_at`.
- **Encapsulated Provider Validation Hooks**: Move all schema verification, state validation, and version compatibility checks out of the core database schema. These features are encapsulated entirely behind the **Provider Adapter** interface via the `validate_session_recovery` contract (for persisted state validation during lazy DB load).
- **Capability-Driven Registry**: Introduce a scalable **Provider Capability Contract** (via a capability container model exposing standard tokens like `ProviderCapability.PERSISTENT_RECOVERY`) allowing the `SessionRegistry` to orchestrate session recovery dynamically and avoid hardcoded provider-type conditionals.
- **Provider-State Versioning**: Establish a dedicated `provider_state_version` key inside the opaque `session_state` JSON payload, allowing provider adapters to validate, migrate, or fail closed on unsupported state schemas.

- **Fail-Closed Session Recovery**: Implement a strict fail-closed recovery lifecycle within the `SessionRegistry` and `RepositoryLayer`. If a conversation snapshot is missing, corrupted, expired, or fails provider-specific recovery validation, the system must raise an explicit recovery error to the client rather than silently creating a replacement conversation.
- **Synchronous Durable Updates**: Ensure strict consistency by making persistence writes synchronous and durable before returning completion or stream responses to the client, preventing context split divergence in the event of immediate process crashes.
- **Decoupled Concurrency and Persistence Orchestration**: Decouple persistence from the request execution coordinator (`SessionManager`). Persistence orchestration belongs exclusively to the `SessionRegistry` acting in coordination with the `RepositoryLayer`.





## Capabilities

### New Capabilities
- `persistent-conversation-layer`: Design and implement a provider-agnostic persistent snapshot schema, repository layer, and recovery lifecycle to achieve restart-safe conversation continuity.

### Modified Capabilities
<!-- None. The existing spec conversation-concurrency-limits represents runtime locks and ownership boundaries, which remain unchanged. The persistence layer integrates with it without modifying its spec-level requirements. -->
- 

## Impact

- **Affected Systems**: `SessionRegistry` (lookup, lazy restoration, and synchronization orchestration) and provider adapters. `SessionManager` remains a decoupled request executor.
- **APIs**: The OpenAI-compatible `/v1/chat/completions` API response and parameters remain unchanged, preserving full backward-compatibility.
- **Database/Storage**: Introduces a new lightweight database layer (SQLite for MVP, migrating to Postgres in future) to hold snapshot states.
- **Dependencies**: No external network dependencies are introduced. Standard Python library serialization and SQLite bindings will be used.

