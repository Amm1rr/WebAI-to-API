# tests/test_sqlite_repository.py
import os
import stat
import sqlite3
from datetime import datetime, timezone, timedelta

import pytest

from app.services.providers.base_repository import ConversationSnapshot
from app.services.providers.exceptions import StateIntegrityError
from app.services.providers.sqlite_repository import SQLiteConversationRepository
from app.services.providers import sqlite_repository as repository_module


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)

@pytest.mark.asyncio
async def test_repository_crud(tmp_path):
    # Use isolated temp file db for unit testing
    db_file = tmp_path / "test_snapshots.db"
    repo = SQLiteConversationRepository(db_path=str(db_file))
    await repo.initialize()

    # 1. Verify get_snapshot on non-existent snapshot returns None
    snapshot = await repo.get_snapshot("non-existent")
    assert snapshot is None

    # 2. Save a new snapshot
    now = datetime.now(timezone.utc)
    original_snapshot = ConversationSnapshot(
        conversation_id="conv-123",
        provider_name="gemini",
        session_state={"metadata": ["cid", "rid", "rcid"], "model_name": "flash"},
        schema_version=1,
        updated_at=now
    )
    await repo.save_snapshot(original_snapshot)

    # 3. Retrieve and assert fields
    retrieved = await repo.get_snapshot("conv-123")
    assert retrieved is not None
    assert retrieved.conversation_id == "conv-123"
    assert retrieved.provider_name == "gemini"
    assert retrieved.session_state == {"metadata": ["cid", "rid", "rcid"], "model_name": "flash"}
    assert retrieved.schema_version == 1
    # Check updated_at with ISO format comparison to avoid timezone object offset discrepancies
    assert retrieved.updated_at.isoformat() == now.isoformat()

    # 4. Update the snapshot
    updated_state = {"metadata": ["cid2", "rid2", "rcid2"], "model_name": "pro"}
    updated_now = datetime.now(timezone.utc)
    updated_snapshot = ConversationSnapshot(
        conversation_id="conv-123",
        provider_name="gemini",
        session_state=updated_state,
        schema_version=1,
        updated_at=updated_now
    )
    await repo.save_snapshot(updated_snapshot)

    # Retrieve and assert updated fields
    retrieved_updated = await repo.get_snapshot("conv-123")
    assert retrieved_updated is not None
    assert retrieved_updated.session_state == updated_state
    assert retrieved_updated.updated_at.isoformat() == updated_now.isoformat()

    # 5. Delete the snapshot
    await repo.delete_snapshot("conv-123")
    deleted = await repo.get_snapshot("conv-123")
    assert deleted is None

@pytest.mark.asyncio
async def test_repository_initializes_nested_parent_directory(tmp_path):
    db_file = tmp_path / "runtime" / "conversations" / "conversation_snapshots.db"
    repo = SQLiteConversationRepository(db_path=str(db_file))

    await repo.initialize()

    assert db_file.exists()

def test_repository_default_db_path_uses_runtime_dir(monkeypatch):
    monkeypatch.setenv("RUNTIME_DIR", "custom_runtime")

    repo = SQLiteConversationRepository()

    assert repo.db_path == "custom_runtime/conversations/conversation_snapshots.db"

@pytest.mark.asyncio
async def test_repository_raises_state_integrity_error_for_corrupted_json(tmp_path):
    db_file = tmp_path / "test_snapshots.db"
    repo = SQLiteConversationRepository(db_path=str(db_file))
    await repo.initialize()

    with sqlite3.connect(str(db_file)) as conn:
        conn.execute(
            """
            INSERT INTO conversation_snapshots (
                conversation_id, provider_name, session_state, schema_version, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "corrupt-json",
                "gemini",
                "{not-json",
                1,
                datetime.now(timezone.utc).isoformat(),
            )
        )
        conn.commit()

    with pytest.raises(StateIntegrityError):
        await repo.get_snapshot("corrupt-json")


@pytest.mark.asyncio
async def test_repository_list_snapshots_sorted_by_updated_at_desc(tmp_path):
    db_file = tmp_path / "test_snapshots.db"
    repo = SQLiteConversationRepository(db_path=str(db_file))
    await repo.initialize()

    base_time = datetime.now(timezone.utc)
    snapshots = [
        ConversationSnapshot(
            conversation_id="old",
            provider_name="gemini",
            session_state={"metadata": ["cid-old", "rid", "rcid"], "model_name": "flash"},
            schema_version=1,
            updated_at=base_time - timedelta(minutes=2),
        ),
        ConversationSnapshot(
            conversation_id="new",
            provider_name="gemini",
            session_state={"metadata": ["cid-new", "rid", "rcid"], "model_name": "pro"},
            schema_version=1,
            updated_at=base_time,
        ),
        ConversationSnapshot(
            conversation_id="middle",
            provider_name="gemini",
            session_state={"metadata": ["cid-middle", "rid", "rcid"], "model_name": "flash"},
            schema_version=1,
            updated_at=base_time - timedelta(minutes=1),
        ),
    ]

    for snapshot in snapshots:
        await repo.save_snapshot(snapshot)

    listed = await repo.list_snapshots()

    assert [snapshot.conversation_id for snapshot in listed] == ["new", "middle", "old"]


@pytest.mark.asyncio
async def test_repository_list_snapshots_filters_by_provider_name(tmp_path):
    db_file = tmp_path / "test_snapshots.db"
    repo = SQLiteConversationRepository(db_path=str(db_file))
    await repo.initialize()

    now = datetime.now(timezone.utc)
    await repo.save_snapshot(ConversationSnapshot(
        conversation_id="gemini-conv",
        provider_name="gemini",
        session_state={"metadata": ["cid", "rid", "rcid"], "model_name": "flash"},
        schema_version=1,
        updated_at=now,
    ))
    await repo.save_snapshot(ConversationSnapshot(
        conversation_id="other-conv",
        provider_name="other",
        session_state={"metadata": ["cid", "rid", "rcid"], "model_name": "other"},
        schema_version=1,
        updated_at=now,
    ))

    listed = await repo.list_snapshots("gemini")

    assert [snapshot.conversation_id for snapshot in listed] == ["gemini-conv"]


@pytest.mark.asyncio
async def test_repository_closes_every_connection_and_preserves_errors(monkeypatch, tmp_path):
    real_connect = repository_module.sqlite3.connect
    connections = []

    class TrackedConnection:
        def __init__(self, connection):
            self.connection = connection
            self.closed = False

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args):
            return self.connection.__exit__(*args)

        def close(self):
            self.closed = True
            return self.connection.close()

        def __getattr__(self, name):
            return getattr(self.connection, name)

    def connect(*args, **kwargs):
        connection = TrackedConnection(real_connect(*args, **kwargs))
        connections.append(connection)
        return connection

    monkeypatch.setattr(repository_module.sqlite3, "connect", connect)

    db_file = tmp_path / "tracked.db"
    repo = SQLiteConversationRepository(db_path=str(db_file))
    await repo.initialize()
    await repo.get_snapshot("missing")
    await repo.save_snapshot(ConversationSnapshot(
        "tracked", "gemini", {"metadata": ["cid", "rid", "rcid"]}, 1, datetime.now(timezone.utc)
    ))
    await repo.list_snapshots()
    await repo.prune_stale_snapshots(datetime.now(timezone.utc) + timedelta(days=1))
    await repo.delete_snapshot("tracked")

    broken_repo = SQLiteConversationRepository(db_path=str(tmp_path / "uninitialized.db"))
    with pytest.raises(sqlite3.OperationalError):
        await broken_repo.get_snapshot("missing")

    assert connections
    assert all(connection.closed for connection in connections)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX mode semantics only")
async def test_default_storage_hardens_missing_runtime_directories_and_db(monkeypatch, tmp_path):
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setenv("RUNTIME_DIR", str(runtime_dir))

    repo = SQLiteConversationRepository()
    await repo.initialize()

    conversations_dir = runtime_dir / "conversations"
    db_file = conversations_dir / "conversation_snapshots.db"
    assert _mode(runtime_dir) == 0o700
    assert _mode(conversations_dir) == 0o700
    assert _mode(db_file) == 0o600


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX mode semantics only")
async def test_default_storage_hardens_existing_parent_and_db_without_overwriting(
    monkeypatch, tmp_path
):
    runtime_dir = tmp_path / "runtime"
    conversations_dir = runtime_dir / "conversations"
    conversations_dir.mkdir(parents=True)
    db_file = conversations_dir / "conversation_snapshots.db"
    connection = sqlite3.connect(str(db_file))
    connection.execute(
        "CREATE TABLE conversation_snapshots ("
        "conversation_id TEXT PRIMARY KEY, provider_name TEXT NOT NULL, "
        "session_state TEXT NOT NULL, schema_version INTEGER NOT NULL, updated_at TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO conversation_snapshots VALUES (?, ?, ?, ?, ?)",
        ("keep", "gemini", '{"metadata": ["cid", "rid", "rcid"]}', 1, "2026-01-01T00:00:00+00:00"),
    )
    connection.commit()
    connection.close()
    os.chmod(runtime_dir, 0o755)
    os.chmod(conversations_dir, 0o755)
    os.chmod(db_file, 0o644)
    monkeypatch.setenv("RUNTIME_DIR", str(runtime_dir))

    SQLiteConversationRepository().initialize_sync()

    assert _mode(runtime_dir) == 0o700
    assert _mode(conversations_dir) == 0o700
    assert _mode(db_file) == 0o600
    connection = sqlite3.connect(str(db_file))
    try:
        assert connection.execute(
            "SELECT session_state FROM conversation_snapshots WHERE conversation_id = 'keep'"
        ).fetchone()[0] == '{"metadata": ["cid", "rid", "rcid"]}'
    finally:
        connection.close()


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX mode semantics only")
async def test_custom_storage_hardens_db_without_changing_existing_parent(tmp_path):
    parent = tmp_path / "shared"
    parent.mkdir()
    os.chmod(parent, 0o755)
    db_file = parent / "snapshots.db"

    repo = SQLiteConversationRepository(db_path=str(db_file))
    await repo.initialize()
    os.chmod(db_file, 0o644)
    await repo.initialize()

    assert _mode(parent) == 0o755
    assert _mode(db_file) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode semantics only")
def test_custom_storage_creates_only_new_parent_private(tmp_path):
    parent = tmp_path / "new" / "shared"
    db_file = parent / "snapshots.db"

    SQLiteConversationRepository(db_path=str(db_file)).initialize_sync()

    assert _mode(parent) == 0o700


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode semantics only")
def test_custom_existing_sidecars_are_hardened_without_changing_parent(tmp_path):
    parent = tmp_path / "shared"
    parent.mkdir()
    os.chmod(parent, 0o755)
    db_file = parent / "snapshots.db"
    SQLiteConversationRepository(db_path=str(db_file)).initialize_sync()
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = str(db_file) + suffix
        if suffix:
            with open(path, "wb") as handle:
                handle.write(b"legacy sidecar")
        os.chmod(path, 0o644)

    repo = SQLiteConversationRepository(db_path=str(db_file))
    repo._ensure_parent_dir()
    repo._ensure_database_file()

    assert _mode(parent) == 0o755
    for suffix in ("", "-wal", "-shm", "-journal"):
        assert _mode(str(db_file) + suffix) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode semantics only")
def test_missing_sidecars_are_harmless(tmp_path):
    db_file = tmp_path / "missing-sidecars.db"

    SQLiteConversationRepository(db_path=str(db_file)).initialize_sync()

    assert not os.path.exists(str(db_file) + "-wal")
    assert not os.path.exists(str(db_file) + "-shm")
    assert not os.path.exists(str(db_file) + "-journal")


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode semantics only")
def test_sidecar_hardening_failure_stops_before_connect(monkeypatch, tmp_path):
    parent = tmp_path / "shared"
    parent.mkdir()
    db_file = parent / "snapshots.db"
    SQLiteConversationRepository(db_path=str(db_file)).initialize_sync()
    sidecar = str(db_file) + "-wal"
    with open(sidecar, "wb") as handle:
        handle.write(b"legacy sidecar")
    os.chmod(sidecar, 0o644)

    repo = SQLiteConversationRepository(db_path=str(db_file))
    real_chmod = repository_module.os.chmod
    real_connect = repository_module.sqlite3.connect
    connect_called = False

    def fail_sidecar(path, mode):
        if path == sidecar:
            raise PermissionError("permission denied")
        real_chmod(path, mode)

    def unexpected_connect(*args, **kwargs):
        nonlocal connect_called
        connect_called = True
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(repository_module.os, "chmod", fail_sidecar)
    monkeypatch.setattr(repository_module.sqlite3, "connect", unexpected_connect)

    with pytest.raises(PermissionError):
        with repo._connection():
            pass
    assert not connect_called


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode semantics only")
def test_sidecar_disappearance_race_is_benign(monkeypatch, tmp_path):
    parent = tmp_path / "shared"
    parent.mkdir()
    db_file = parent / "snapshots.db"
    SQLiteConversationRepository(db_path=str(db_file)).initialize_sync()
    sidecar = str(db_file) + "-wal"
    with open(sidecar, "wb") as handle:
        handle.write(b"legacy sidecar")
    os.chmod(sidecar, 0o644)

    repo = SQLiteConversationRepository(db_path=str(db_file))
    real_chmod = repository_module.os.chmod

    def disappear_sidecar(path, mode):
        if path == sidecar:
            os.unlink(path)
            raise FileNotFoundError(path)
        real_chmod(path, mode)

    monkeypatch.setattr(repository_module.os, "chmod", disappear_sidecar)
    repo._ensure_parent_dir()
    repo._ensure_database_file()

    assert not os.path.exists(sidecar)


def test_memory_database_path_is_not_materialized(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    SQLiteConversationRepository(db_path=":memory:").initialize_sync()

    assert not (tmp_path / ":memory:").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode semantics only")
def test_wal_and_shm_inherit_secure_main_db_mode(tmp_path):
    db_file = tmp_path / "wal.db"
    repo = SQLiteConversationRepository(db_path=str(db_file))
    repo.initialize_sync()

    with repo._connection() as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        connection.execute(
            "INSERT INTO conversation_snapshots VALUES (?, ?, ?, ?, ?)",
            ("wal", "gemini", '{"metadata": ["cid", "rid", "rcid"]}', 1, "2026-01-01T00:00:00+00:00"),
        )
        connection.commit()
        assert _mode(db_file) == 0o600
        assert _mode(str(db_file) + "-wal") == 0o600
        assert _mode(str(db_file) + "-shm") == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode semantics only")
def test_default_storage_hardening_failure_propagates(monkeypatch, tmp_path):
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setenv("RUNTIME_DIR", str(runtime_dir))
    repo = SQLiteConversationRepository()
    real_chmod = repository_module.os.chmod

    def fail_on_runtime(path, mode):
        if os.path.abspath(path) == os.path.abspath(runtime_dir):
            raise PermissionError("permission denied")
        real_chmod(path, mode)

    monkeypatch.setattr(repository_module.os, "chmod", fail_on_runtime)

    with pytest.raises(PermissionError):
        repo.initialize_sync()
    assert not (runtime_dir / "conversations" / "conversation_snapshots.db").exists()
