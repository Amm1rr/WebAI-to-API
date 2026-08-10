# src/app/services/providers/base_repository.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from typing import Optional, Dict, Any, List

class ProviderCapability(Enum):
    PERSISTENT_RECOVERY = auto()

@dataclass
class ConversationSnapshot:
    conversation_id: str
    provider_name: str
    session_state: Dict[str, Any]  # Opaque provider-specific dictionary
    schema_version: int
    updated_at: datetime


class IConversationRepository(ABC):
    """
    Abstract interface for conversation snapshot persistence.
    Treats the snapshot session state as opaque provider-owned data.
    """

    @abstractmethod
    async def get_snapshot(self, conversation_id: str) -> Optional[ConversationSnapshot]:
        """
        Retrieve a conversation snapshot by conversation_id.
        Returns None if not found.
        """
        pass

    @abstractmethod
    async def save_snapshot(self, snapshot: ConversationSnapshot) -> None:
        """
        Save or update a conversation snapshot.
        """
        pass

    @abstractmethod
    async def delete_snapshot(self, conversation_id: str) -> None:
        """
        Delete a conversation snapshot by conversation_id.
        """
        pass

    @abstractmethod
    async def list_snapshots(self, provider_name: Optional[str] = None) -> List[ConversationSnapshot]:
        """
        List conversation snapshots, optionally filtered by provider name.
        Results are ordered by most recently updated first.
        """
        pass

    async def prune_stale_snapshots(self, cutoff: datetime) -> int:
        """
        Delete snapshots last updated before cutoff.
        Returns the number of deleted rows.
        """
        return 0
