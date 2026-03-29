# src/app/services/log_broadcaster.py
import asyncio
import logging
from collections import deque
from datetime import datetime
from typing import AsyncGenerator, Optional


class LogEntry:
    """Structured log entry for the admin UI."""

    __slots__ = ("timestamp", "level", "name", "message", "id")

    def __init__(self, record: logging.LogRecord, entry_id: int):
        self.timestamp = datetime.fromtimestamp(record.created).isoformat()
        self.level = record.levelname
        self.name = record.name
        self.message = record.getMessage()
        self.id = entry_id

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "level": self.level,
            "logger": self.name,
            "message": self.message,
        }


class SSELogBroadcaster:
    """
    Singleton that captures log records and broadcasts them to SSE clients.
    Uses a ring buffer (deque) for recent entries and asyncio.Event to wake subscribers.
    """

    _instance: Optional["SSELogBroadcaster"] = None

    def __init__(self, max_entries: int = 500):
        self._buffer: deque[LogEntry] = deque(maxlen=max_entries)
        self._counter: int = 0
        self._event = asyncio.Event()
        self._clients: int = 0

    @classmethod
    def get_instance(cls) -> "SSELogBroadcaster":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def push(self, record: logging.LogRecord) -> None:
        """Called by the logging handler (may be from any thread)."""
        self._counter += 1
        entry = LogEntry(record, self._counter)
        self._buffer.append(entry)
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(self._event.set)
        except RuntimeError:
            pass

    def get_recent(self, count: int = 50) -> list[dict]:
        """Return the most recent N log entries as dicts."""
        entries = list(self._buffer)[-count:]
        return [e.to_dict() for e in entries]

    async def subscribe(self, last_id: int = 0) -> AsyncGenerator[dict, None]:
        """Async generator that yields new log entries as they arrive."""
        self._clients += 1
        try:
            # Replay buffered entries newer than last_id
            for entry in self._buffer:
                if entry.id > last_id:
                    yield entry.to_dict()
                    last_id = entry.id

            # Live tail
            while True:
                self._event.clear()
                await self._event.wait()
                for entry in self._buffer:
                    if entry.id > last_id:
                        yield entry.to_dict()
                        last_id = entry.id
        finally:
            self._clients -= 1

    @property
    def client_count(self) -> int:
        return self._clients


class BroadcastLogHandler(logging.Handler):
    """Logging handler that forwards records to the SSELogBroadcaster."""

    def __init__(self, broadcaster: SSELogBroadcaster):
        super().__init__()
        self.broadcaster = broadcaster

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.broadcaster.push(record)
        except Exception:
            self.handleError(record)
