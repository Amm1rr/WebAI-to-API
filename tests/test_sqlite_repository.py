# tests/test_sqlite_repository.py
import pytest
import sqlite3
from datetime import datetime, timezone
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
