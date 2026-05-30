# src/app/services/providers/sqlite_repository.py
import sqlite3
import json
import asyncio
import os
from datetime import datetime
from typing import Optional
from app.config import get_default_conversation_snapshot_db
from app.logger import logger
from app.services.providers.base_repository import IConversationRepository, ConversationSnapshot
from app.services.providers.exceptions import StateIntegrityError

class SQLiteConversationRepository(IConversationRepository):
    """
    SQLite implementation of the IConversationRepository.
    Uses WAL mode for high concurrency write transaction safety and runs blocking I/O
    inside thread pools to keep event loop unblocked.
    """
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or get_default_conversation_snapshot_db()

    def _ensure_parent_dir(self) -> None:
        parent_dir = os.path.dirname(self.db_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

    def _execute_write(self, query: str, params: tuple = ()) -> None:
        self._ensure_parent_dir()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=FULL;")
            conn.execute(query, params)
            conn.commit()

    def _execute_read_one(self, query: str, params: tuple = ()) -> Optional[tuple]:
        self._ensure_parent_dir()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.fetchone()

    async def initialize(self) -> None:
        """Create database tables and set WAL mode."""
        await asyncio.to_thread(self.initialize_sync)

    def initialize_sync(self) -> None:
        """Synchronously create database tables and set WAL mode."""
        self._ensure_parent_dir()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=FULL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_snapshots (
                    conversation_id TEXT PRIMARY KEY,
                    provider_name TEXT NOT NULL,
                    session_state TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()
        logger.info(f"SQLiteConversationRepository initialized at {self.db_path} in WAL mode.")

    async def get_snapshot(self, conversation_id: str) -> Optional[ConversationSnapshot]:
        """Retrieve a conversation snapshot by conversation_id."""
        def _get():
            row = self._execute_read_one(
                "SELECT conversation_id, provider_name, session_state, schema_version, updated_at FROM conversation_snapshots WHERE conversation_id = ?",
                (conversation_id,)
            )
            if not row:
                return None
            try:
                state_dict = json.loads(row[2])
                updated_dt = datetime.fromisoformat(row[4])
                return ConversationSnapshot(
                    conversation_id=row[0],
                    provider_name=row[1],
                    session_state=state_dict,
                    schema_version=row[3],
                    updated_at=updated_dt
                )
            except Exception as e:
                logger.error(f"Error deserializing conversation snapshot {conversation_id}: {e}", exc_info=True)
                raise StateIntegrityError(f"Corrupted conversation snapshot: {conversation_id}") from e
        return await asyncio.to_thread(_get)

    async def save_snapshot(self, snapshot: ConversationSnapshot) -> None:
        """Save or update a conversation snapshot."""
        def _save():
            state_str = json.dumps(snapshot.session_state)
            updated_str = snapshot.updated_at.isoformat()
            self._execute_write(
                """
                INSERT OR REPLACE INTO conversation_snapshots (
                    conversation_id, provider_name, session_state, schema_version, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    snapshot.conversation_id,
                    snapshot.provider_name,
                    state_str,
                    snapshot.schema_version,
                    updated_str
                )
            )
        await asyncio.to_thread(_save)

    async def delete_snapshot(self, conversation_id: str) -> None:
        """Delete a conversation snapshot."""
        def _delete():
            self._execute_write(
                "DELETE FROM conversation_snapshots WHERE conversation_id = ?",
                (conversation_id,)
            )
        await asyncio.to_thread(_delete)

    async def prune_stale_snapshots(self, cutoff: datetime) -> int:
        """Delete snapshots older than cutoff and return the number of rows deleted."""
        def _prune():
            self._ensure_parent_dir()
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=FULL;")
                cursor = conn.execute(
                    "DELETE FROM conversation_snapshots WHERE updated_at < ?",
                    (cutoff.isoformat(),)
                )
                conn.commit()
                return cursor.rowcount

        return await asyncio.to_thread(_prune)
