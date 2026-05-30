# src/app/services/session_manager.py
import asyncio
import time
import secrets
from typing import Dict, Optional, Any, AsyncGenerator, List
from app.logger import logger
from app.services.gemini_client import get_gemini_client, GeminiClientNotInitializedError

# Configuration constants
MAX_SESSIONS = 500
IDLE_TIMEOUT = 3600  # 60 minutes in seconds
MAX_GENERATION_DURATION = 300  # 5 minutes in seconds

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
                            yield {
                                "type": "chunk",
                                "text_delta": getattr(chunk, 'text_delta', ""),
                                "is_reused": is_reused
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
    def __init__(self, client):
        self.client = client
        self._sessions: Dict[str, SessionManager] = {}
        self._lock = asyncio.Lock() # Registry-level lock for atomic lookup-or-create

    async def get_session(self, conversation_id: str) -> SessionManager:
        """Retrieve or create a session manager. Triggers passive cleanup."""
        async with self._lock:
            # 1. Passive Cleanup if capacity exceeded
            if len(self._sessions) >= MAX_SESSIONS:
                self._prune_sessions()

            # 2. Lookup or create
            if conversation_id not in self._sessions:
                if len(self._sessions) >= MAX_SESSIONS:
                    from fastapi import HTTPException
                    raise HTTPException(
                        status_code=429, 
                        detail="Server at capacity. No available chat sessions. Please try again later."
                    )
                
                self._sessions[conversation_id] = SessionManager(self.client)
            
            return self._sessions[conversation_id]

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

def generate_opaque_token() -> str:
    """Generate a cryptographically secure opaque token for conversation IDs."""
    return secrets.token_urlsafe(16)

# Global instances
_translate_session_manager = None
_gemini_chat_registry = None

def init_session_managers():
    """
    Initialize session managers. 
    /translate keeps its singleton legacy manager.
    /gemini-chat moves to the new SessionRegistry.
    """
    global _translate_session_manager, _gemini_chat_registry
    try:
        client = get_gemini_client()
        _translate_session_manager = SessionManager(client)
        _gemini_chat_registry = SessionRegistry(client)
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

