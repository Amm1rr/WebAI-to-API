# src/app/services/providers/gemini/session_manager.py
import asyncio
import time
import secrets
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Any, AsyncGenerator, List
from app.config import get_default_conversation_snapshot_db
from app.logger import logger
from app.services.providers.gemini.client import get_gemini_client, GeminiClientNotInitializedError
from app.services.providers.base_repository import ConversationSnapshot, IConversationRepository, ProviderCapability
from app.services.providers.exceptions import (
    ConversationInUseError,
    SnapshotNotFoundError,
    StateIntegrityError,
)
from app.utils.tokens import generate_opaque_token

# Configuration constants
MAX_SESSIONS = 500
IDLE_TIMEOUT = 3600  # 60 minutes in seconds
MAX_GENERATION_DURATION = 300  # 5 minutes in seconds
SNAPSHOT_SCHEMA_VERSION = 1
RETENTION_PERIOD_DAYS = int(os.getenv("CONVERSATION_RETENTION_DAYS", "90"))

class SessionManager:
    def __init__(self, client):
        self.client = client
        self.session = None
        self.model = None
        self.gem = None
        self.lock = asyncio.Lock()
        self.last_accessed = time.time()
        self.active_streams = 0 # Atomic counter for safe pruning

    async def get_response(self, model, message, images, gem=None):
        async with self.lock:
            try:
                self.last_accessed = time.time() # Update at start
                self._ensure_session(model, gem)
                response = await self.session.send_message(prompt=message, files=images)
                return response
            except Exception as e:
                logger.error(f"Error in session get_response: {e}", exc_info=True)
                raise
            finally:
                self.last_accessed = time.time() # Update at end

    async def get_streaming_response(self, model, message, images, gem=None) -> AsyncGenerator[Any, None]:
        """
        Extended lock scope generator for progressive streaming.
        Ensures exactly-once interruption metadata and safe lock release.
        """
        self.active_streams += 1
        interrupted_sent = False
        
        async with self.lock:
            try:
                self.last_accessed = time.time()
                self._ensure_session(model, gem)
                
                try:
                    async with asyncio.timeout(MAX_GENERATION_DURATION):
                        async for chunk in self.session.send_message_stream(prompt=message, files=images):
                            yield {
                                "type": "chunk",
                                "text_delta": getattr(chunk, 'text_delta', "")
                            }
                except asyncio.TimeoutError:
                    logger.warning("Stream exceeded MAX_GENERATION_DURATION, interrupting.")
                    interrupted_sent = True
                    yield {
                        "type": "interrupt",
                        "interrupted": True,
                        "reason": "timeout"
                    }
                
            except (asyncio.CancelledError, GeneratorExit):
                logger.info("Client disconnected or generator closed.")
                # We can't yield here if it's GeneratorExit, but we can try for CancelledError
                # However, to be safe and avoid multi-yield issues, we just propagate.
                # The endpoint will handle adding the 'interrupted' signal if needed.
                raise
            except Exception as e:
                logger.error(f"Error in session streaming: {e}", exc_info=True)
                if not interrupted_sent:
                    yield {
                        "type": "interrupt",
                        "interrupted": True,
                        "reason": str(e)
                    }
            finally:
                self.active_streams -= 1
                self.last_accessed = time.time()

    async def get_response_stateful(self, model, messages, tools_prompt, files=None, gem=None):
        """
        Thread-safe stateful response execution.
        Resolves whether to reuse or bootstrap the session within the lock.
        """
        async with self.lock:
            try:
                self.last_accessed = time.time()
                is_reused = (self.session is not None and self.model == model and self.gem == gem)
                
                self._ensure_session(model, gem)
                
                if is_reused:
                    prompt = messages[-1].get("content", "")
                else:
                    conversation_parts = transform_messages(messages, tools_prompt)
                    prompt = "\n\n".join(conversation_parts)
                
                response = await self.session.send_message(prompt=prompt, files=files)
                return response, is_reused
            except Exception as e:
                logger.error(f"Error in stateful session get_response: {e}", exc_info=True)
                raise
            finally:
                self.last_accessed = time.time()

    async def get_streaming_response_stateful(self, model, messages, tools_prompt, files=None, gem=None) -> AsyncGenerator[Any, None]:
        """
        Thread-safe stateful progressive streaming response execution.
        Safely increments active streams and yields chunks with locked timeout protection.
        """
        self.active_streams += 1
        interrupted_sent = False
        final_response = None
        
        async with self.lock:
            try:
                self.last_accessed = time.time()
                is_reused = (self.session is not None and self.model == model and self.gem == gem)
                
                self._ensure_session(model, gem)
                
                if is_reused:
                    prompt = messages[-1].get("content", "")
                else:
                    conversation_parts = transform_messages(messages, tools_prompt)
                    prompt = "\n\n".join(conversation_parts)
                
                try:
                    async with asyncio.timeout(MAX_GENERATION_DURATION):
                        async for chunk in self.session.send_message_stream(prompt=prompt, files=files):
                            final_response = chunk
                            yield {
                                "type": "chunk",
                                "text_delta": getattr(chunk, 'text_delta', ""),
                                "is_reused": is_reused
                            }
                    if final_response is not None:
                        yield {
                            "type": "final",
                            "response": final_response,
                            "is_reused": is_reused,
                        }
                except asyncio.TimeoutError:
                    logger.warning("Stream exceeded MAX_GENERATION_DURATION, interrupting.")
                    interrupted_sent = True
                    yield {
                        "type": "interrupt",
                        "interrupted": True,
                        "reason": "timeout",
                        "is_reused": is_reused
                    }
            except (asyncio.CancelledError, GeneratorExit):
                logger.info("Client disconnected or generator closed.")
                raise
            except Exception as e:
                logger.error(f"Error in stateful session streaming: {e}", exc_info=True)
                if not interrupted_sent:
                    yield {
                        "type": "interrupt",
                        "interrupted": True,
                        "reason": str(e),
                        "is_reused": is_reused
                    }
            finally:
                self.active_streams -= 1
                self.last_accessed = time.time()

    def _ensure_session(self, model, gem):
        """Internal helper to start or switch session if needed. Must be called inside self.lock."""
        if self.session is None or self.model != model or self.gem != gem:
            model_value = model.value if hasattr(model, "value") else model
            self.session = self.client.start_chat(model=model_value, gem=gem)
            self.model = model
            self.gem = gem


class SessionRegistry:
    """
    Manages a collection of SessionManager instances keyed by conversation_id.
    Implements atomic creation and active-stream aware pruning.
    """
    def __init__(self, client, repository: Optional[IConversationRepository] = None):
        self.client = client
        self.repository = repository
        self._sessions: Dict[str, SessionManager] = {}
        self._deleting: set[str] = set()
        self._lock = asyncio.Lock() # Registry-level lock for atomic lookup-or-create

    async def update_client(self, client):
        """
        Safely update the direct client reference in all active session managers
        under the registry-level management lock. This guarantees coroutine-level
        serialized updates to prevent race conditions with concurrent session creation,
        but does not represent low-level hardware or CPU-level atomicity.
        """
        async with self._lock:
            self.client = client
            for manager in self._sessions.values():
                manager.client = client

    async def get_session(
        self,
        conversation_id: str,
        provider_adapter: Optional[Any] = None,
        *,
        allow_create: bool = True,
        model: Optional[Any] = None,
        gem: Optional[Any] = None,
    ) -> SessionManager:
        """Retrieve, restore, or create a session manager. Triggers passive cleanup."""
        async with self._lock:
            if conversation_id in self._deleting:
                raise ConversationInUseError(f"Conversation is currently being deleted: {conversation_id}")

            # 1. Passive Cleanup if capacity exceeded
            if len(self._sessions) >= MAX_SESSIONS:
                self._prune_sessions()

            # 2. Lookup or create
            if conversation_id not in self._sessions:
                if not allow_create:
                    manager = await self._restore_session(conversation_id, provider_adapter, model=model, gem=gem)
                    self._sessions[conversation_id] = manager
                    return manager

                if len(self._sessions) >= MAX_SESSIONS:
                    from fastapi import HTTPException
                    raise HTTPException(
                        status_code=429, 
                        detail="Server at capacity. No available chat sessions. Please try again later."
                    )
                
                self._sessions[conversation_id] = SessionManager(self.client)
            
            return self._sessions[conversation_id]

    async def begin_delete_session(self, conversation_id: str) -> None:
        """
        Reserve a conversation for deletion and block concurrent reuse.

        The tombstone is intentionally held across remote delete I/O by the
        caller, while the registry lock is only held for local state checks.
        """
        async with self._lock:
            if conversation_id in self._deleting:
                raise ConversationInUseError(f"Conversation is currently being deleted: {conversation_id}")

            manager = self._sessions.get(conversation_id)
            if manager and (manager.lock.locked() or manager.active_streams > 0):
                raise ConversationInUseError(f"Conversation is currently in use: {conversation_id}")

            self._deleting.add(conversation_id)

    async def complete_delete_session(self, conversation_id: str) -> None:
        """
        Remove local in-memory and persistent state for a reserved deletion.

        The deletion tombstone is always cleared so failed local cleanup does
        not permanently block the conversation ID.
        """
        try:
            if self.repository:
                await self.repository.delete_snapshot(conversation_id)

            async with self._lock:
                self._sessions.pop(conversation_id, None)
        finally:
            async with self._lock:
                self._deleting.discard(conversation_id)

    async def abort_delete_session(self, conversation_id: str) -> None:
        """Release a deletion reservation after a pre-cleanup failure."""
        async with self._lock:
            self._deleting.discard(conversation_id)

    async def save_session_snapshot(self, conversation_id: str, provider_adapter: Any, manager: SessionManager) -> None:
        """
        Persist a session snapshot synchronously after a successful turn.

        Persistence is fail-closed by contract: repository or serialization errors
        intentionally propagate so callers do not return a successful response with
        stale durable state.
        """
        if not self.repository or not self._supports_persistent_recovery(provider_adapter):
            return
        if manager.session is None:
            raise StateIntegrityError("Cannot persist an empty session.")
        provider_name = self._provider_name(provider_adapter)

        session_state = provider_adapter.serialize_session_state(manager.session)
        if isinstance(session_state, str):
            import json
            session_state = json.loads(session_state)

        snapshot = ConversationSnapshot(
            conversation_id=conversation_id,
            provider_name=provider_name,
            session_state=session_state,
            schema_version=SNAPSHOT_SCHEMA_VERSION,
            updated_at=datetime.now(timezone.utc),
        )
        await self.repository.save_snapshot(snapshot)
        manager.last_accessed = time.time()

    async def prune_stale_snapshots(self) -> int:
        """Prune inactive persistent snapshots using the configured retention period."""
        if not self.repository:
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_PERIOD_DAYS)
        return await self.repository.prune_stale_snapshots(cutoff)

    async def list_conversation_snapshots(self, provider_name: str = "gemini") -> List[ConversationSnapshot]:
        """List persisted conversation snapshots for the requested provider."""
        if not self.repository:
            raise SnapshotNotFoundError("Conversation snapshot repository is not initialized.")
        return await self.repository.list_snapshots(provider_name)

    async def _restore_session(
        self,
        conversation_id: str,
        provider_adapter: Optional[Any],
        *,
        model: Optional[Any] = None,
        gem: Optional[Any] = None,
    ) -> SessionManager:
        if not self.repository:
            raise SnapshotNotFoundError(f"Conversation snapshot not found: {conversation_id}")
        if not self._supports_persistent_recovery(provider_adapter):
            raise StateIntegrityError("Provider does not support persistent recovery.")

        snapshot = await self.repository.get_snapshot(conversation_id)
        if snapshot is None:
            raise SnapshotNotFoundError(f"Conversation snapshot not found: {conversation_id}")
        provider_name = self._provider_name(provider_adapter)
        if snapshot.provider_name != provider_name:
            raise StateIntegrityError("Snapshot provider does not match registry provider.")
        if snapshot.schema_version != SNAPSHOT_SCHEMA_VERSION:
            raise StateIntegrityError(f"Unsupported snapshot schema version: {snapshot.schema_version}")

        validated_state = provider_adapter.validate_session_recovery(
            snapshot.session_state,
            {"conversation_id": conversation_id, "model": model, "gem": gem},
        )
        session = provider_adapter.deserialize_session_state(
            validated_state,
            self.client,
            model=model,
            gem=gem,
        )

        manager = SessionManager(self.client)
        manager.session = session
        manager.model = model if model is not None else getattr(session, "model", None)
        manager.gem = gem if gem is not None else getattr(session, "gem", None)
        manager.last_accessed = time.time()
        return manager

    def _supports_persistent_recovery(self, provider_adapter: Optional[Any]) -> bool:
        capabilities = getattr(provider_adapter, "capabilities", set()) if provider_adapter else set()
        return ProviderCapability.PERSISTENT_RECOVERY in capabilities

    def _provider_name(self, provider_adapter: Optional[Any]) -> str:
        provider_name = getattr(provider_adapter, "provider_name", None) if provider_adapter else None
        if not isinstance(provider_name, str) or not provider_name:
            raise StateIntegrityError("Provider does not declare a valid provider_name.")
        return provider_name

    def _prune_sessions(self):
        """Remove idle, unlocked, and unpinned sessions from the registry."""
        now = time.time()
        # Snapshot keys to avoid concurrent mutation issues
        candidates: List[tuple[str, SessionManager]] = sorted(
            list(self._sessions.items()),
            key=lambda x: x[1].last_accessed
        )

        for cid, manager in candidates:
            # SAFETY: Only prune if:
            # 1. Lock is not held (no active processing)
            # 2. active_streams == 0 (no pinned generators)
            # 3. Session has actually been used at least once or reached TTL
            if not manager.lock.locked() and manager.active_streams == 0:
                if now - manager.last_accessed > IDLE_TIMEOUT or len(self._sessions) >= MAX_SESSIONS:
                    del self._sessions[cid]
                    # Target reclaiming 10% buffer
                    if len(self._sessions) < MAX_SESSIONS * 0.9:
                        break


# Global instances
_translate_session_manager = None
_gemini_chat_registry = None

async def init_session_managers():
    """
    Initialize session managers. 
    /translate keeps its singleton legacy manager.
    /gemini-chat moves to the new SessionRegistry.
    If already initialized, safely updates client references in all active 
    session managers and registries to preserve runtime/concurrency state.
    """
    global _translate_session_manager, _gemini_chat_registry
    try:
        client = get_gemini_client()
        
        # If already initialized, safely update their client references to preserve runtime state
        if _translate_session_manager is not None and _gemini_chat_registry is not None:
            _translate_session_manager.client = client
            await _gemini_chat_registry.update_client(client)
            logger.info("Session managers safely updated with new client reference.")
            return

        from app.services.providers.sqlite_repository import SQLiteConversationRepository

        repository = SQLiteConversationRepository(
            db_path=os.getenv("CONVERSATION_SNAPSHOT_DB", get_default_conversation_snapshot_db())
        )
        repository.initialize_sync()
        _translate_session_manager = SessionManager(client)
        _gemini_chat_registry = SessionRegistry(client, repository=repository)
    except GeminiClientNotInitializedError:
        logger.warning("Session managers not initialized: Gemini client not available.")

def get_translate_session_manager():
    return _translate_session_manager

def get_gemini_chat_registry():
    return _gemini_chat_registry


def transform_messages(messages: list[dict], tools_prompt: str = "") -> list[str]:
    """
    Format list of OpenAI messages into standard Gemini prompt lines.
    """
    conversation_parts = []
    messages_copy = [msg.copy() for msg in messages]
    
    system_msg_index = -1
    for i, msg in enumerate(messages_copy):
        if msg.get("role") == "system":
            system_msg_index = i
            break

    if tools_prompt:
        if system_msg_index != -1:
            orig_content = messages_copy[system_msg_index].get("content") or ""
            messages_copy[system_msg_index]["content"] = f"{orig_content}\n\n{tools_prompt}".strip()
        else:
            conversation_parts.append(tools_prompt)

    for msg in messages_copy:
        role = msg.get("role", "user")
        content = msg.get("content") or ""

        if role == "system":
            conversation_parts.append(f"System: {content}")
        elif role == "user":
            conversation_parts.append(f"User: {content}")
        elif role == "assistant":
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    conversation_parts.append(
                        f"Assistant called tool {fn.get('name')}: {fn.get('arguments', '')}"
                    )
            elif content:
                conversation_parts.append(f"Assistant: {content}")
        elif role == "tool":
            tool_call_id = msg.get("tool_call_id", "")
            conversation_parts.append(f"Tool result [{tool_call_id}]: {content}")
    
    return conversation_parts
