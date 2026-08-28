# src/app/services/providers/sqlite_repository.py
import sqlite3
import json
import asyncio
import os
from contextlib import contextmanager
from datetime import datetime
from typing import Optional, List
from app.config import get_default_conversation_snapshot_db, get_runtime_dir
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
        default_db_path = get_default_conversation_snapshot_db()
        self.db_path = os.fspath(db_path) if db_path else default_db_path
        self._default_db_path = os.path.abspath(default_db_path)
        self._is_project_owned_path = os.path.abspath(self.db_path) == self._default_db_path

    def _ensure_parent_dir(self) -> None:
        if self.db_path == ":memory:":
            return
        parent_dir = os.path.dirname(self.db_path)
        if not parent_dir:
            return

        if self._is_project_owned_path:
            runtime_dir = os.path.abspath(get_runtime_dir())
            directories = dict.fromkeys((runtime_dir, parent_dir))
            for directory in directories:
                self._ensure_directory(directory, harden_existing=True)
        else:
            # Custom paths may intentionally use shared existing directories.
            self._ensure_directory(parent_dir, harden_existing=False)

    @staticmethod
    def _ensure_directory(path: str, *, harden_existing: bool) -> None:
        if os.name == "posix":
            os.makedirs(path, mode=0o700, exist_ok=True)
            if harden_existing:
                os.chmod(path, 0o700)
        else:
            os.makedirs(path, exist_ok=True)

    def _ensure_database_file(self) -> None:
        if os.name != "posix" or self.db_path == ":memory:":
            return

        fd = os.open(self.db_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        self._harden_existing_sidecars()

    def _harden_existing_sidecars(self) -> None:
        if os.name != "posix" or self.db_path == ":memory:":
            return

        parent_dir = os.path.dirname(os.path.abspath(self.db_path))
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar_path = self.db_path + suffix
            if not os.path.exists(sidecar_path):
                continue
            try:
                os.chmod(sidecar_path, 0o600)
            except FileNotFoundError:
                # SQLite may remove a sidecar between the existence check and chmod.
                if os.path.exists(sidecar_path) or not os.path.isdir(parent_dir):
                    raise

    @contextmanager
    def _connection(self):
        self._ensure_parent_dir()
        self._ensure_database_file()
        conn = sqlite3.connect(self.db_path)
        original_error = None
        try:
            with conn:
                yield conn
        except BaseException as error:
            original_error = error
            raise
        finally:
            try:
                conn.close()
            except Exception as close_error:
                if original_error is None:
                    raise
                logger.warning(
                    "Failed to close SQLite connection for %s after database error: %s",
                    self.db_path,
                    close_error,
                )

    def _execute_write(self, query: str, params: tuple = ()) -> None:
        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=FULL;")
            conn.execute(query, params)
            conn.commit()

    def _execute_read_one(self, query: str, params: tuple = ()) -> Optional[tuple]:
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.fetchone()

    def _row_to_snapshot(self, row: tuple) -> ConversationSnapshot:
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
            conversation_id = row[0] if row else "<unknown>"
            logger.error(f"Error deserializing conversation snapshot {conversation_id}: {e}", exc_info=True)
            raise StateIntegrityError(f"Corrupted conversation snapshot: {conversation_id}") from e

    async def initialize(self) -> None:
        """Create database tables and set WAL mode."""
        await asyncio.to_thread(self.initialize_sync)

    def initialize_sync(self) -> None:
        """Synchronously create database tables and set WAL mode."""
        with self._connection() as conn:
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
            return self._row_to_snapshot(row)
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

    async def list_snapshots(self, provider_name: Optional[str] = None) -> List[ConversationSnapshot]:
        """List conversation snapshots ordered by updated_at descending."""
        def _list():
            with self._connection() as conn:
                cursor = conn.cursor()
                if provider_name is None:
                    cursor.execute(
                        """
                        SELECT conversation_id, provider_name, session_state, schema_version, updated_at
                        FROM conversation_snapshots
                        ORDER BY updated_at DESC
                        """
                    )
                else:
                    cursor.execute(
                        """
                        SELECT conversation_id, provider_name, session_state, schema_version, updated_at
                        FROM conversation_snapshots
                        WHERE provider_name = ?
                        ORDER BY updated_at DESC
                        """,
                        (provider_name,)
                    )
                return [self._row_to_snapshot(row) for row in cursor.fetchall()]

        return await asyncio.to_thread(_list)

    async def prune_stale_snapshots(self, cutoff: datetime) -> int:
        """Delete snapshots older than cutoff and return the number of rows deleted."""
        def _prune():
            with self._connection() as conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=FULL;")
                cursor = conn.execute(
                    "DELETE FROM conversation_snapshots WHERE updated_at < ?",
                    (cutoff.isoformat(),)
                )
                conn.commit()
                return cursor.rowcount

        return await asyncio.to_thread(_prune)
