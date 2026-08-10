# tests/test_sqlite_repository.py
import pytest
import sqlite3
from datetime import datetime, timezone, timedelta
from app.services.providers.base_repository import ConversationSnapshot
from app.services.providers.exceptions import StateIntegrityError
from app.services.providers.sqlite_repository import SQLiteConversationRepository

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
