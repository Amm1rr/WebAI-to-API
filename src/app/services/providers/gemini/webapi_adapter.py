import asyncio
import json
from typing import Any, List, Optional
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from gemini_webapi.exceptions import APIError, AuthError, TimeoutError as GeminiTimeoutError

from app.services.providers.gemini.client import get_gemini_client, GeminiClientNotInitializedError
from app.services.providers.gemini.session_manager import (
    SNAPSHOT_SCHEMA_VERSION,
    get_gemini_chat_registry,
)
from app.services.providers.exceptions import (
    ConversationInUseError,
    SessionRecoveryError,
    SnapshotNotFoundError,
    StateIntegrityError,
)
from app.services.providers.gemini.base_adapter import GeminiBackendAdapter
from app.services.providers.gemini.shared import (
    convert_to_openai_format, 
    parse_tool_call,
    UNRECOVERABLE_CONVERSATION_ERROR_CODES
)
from app.services.providers.gemini.persistence import (
    serialize_session_state,
    deserialize_session_state,
    validate_session_state_payload
)
from app.services.multimodal import cleanup_staged_files
from app.logger import logger
from app.schemas.request import OpenAIChatRequest

class GeminiWebAPIAdapter(GeminiBackendAdapter):
    """
    Backend adapter for Google Gemini using the gemini-webapi library.
    Handles stateful sessions via SessionRegistry and SQLite.
    """

    def __init__(self, provider):
        self.provider = provider

    def _get_available_gemini_client(self):
        try:
            gemini_client = get_gemini_client()
        except GeminiClientNotInitializedError as e:
            raise HTTPException(status_code=503, detail=str(e))

        account_status = getattr(gemini_client.client, "account_status", None)
        status_name = getattr(account_status, "name", "UNKNOWN") if account_status else "UNKNOWN"
        if status_name != "AVAILABLE":
            logger.warning(f"Gemini client account status is '{status_name}'.")
            if status_name == "UNAUTHENTICATED":
                raise HTTPException(
                    status_code=401,
                    detail="The provided conversation_id requires an authenticated Gemini session. Please sign in and try again.",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            raise HTTPException(
                status_code=401 if status_name == "UNKNOWN" else 503,
                detail=f"Gemini client is not ready (status: {status_name}).",
            )

        return gemini_client

    def _get_normalized_payload(self, request: OpenAIChatRequest):
        normalized = getattr(request, "_normalized_openai_chat_messages", None)
        if normalized is None:
            raise HTTPException(status_code=500, detail="Multimodal request payload was not normalized.")
        return normalized

    async def list_conversations(self) -> dict:
        registry = get_gemini_chat_registry()
        if not registry or not registry.repository:
            raise HTTPException(status_code=503, detail="Session registry is not initialized.")

        try:
            snapshots = await registry.list_conversation_snapshots(self.provider.provider_name)
            data = []
            for snapshot in snapshots:
                if snapshot.provider_name != self.provider.provider_name:
                    raise StateIntegrityError("Snapshot provider does not match registry provider.")
                if snapshot.schema_version != SNAPSHOT_SCHEMA_VERSION:
                    raise StateIntegrityError(f"Unsupported snapshot schema version: {snapshot.schema_version}")

                validated_state = self.provider.validate_session_recovery(
                    snapshot.session_state,
                    {"conversation_id": snapshot.conversation_id},
                )
                data.append({
                    "id": snapshot.conversation_id,
                    "object": "conversation",
                    "provider": self.provider.provider_name,
                    "backend": "webapi",
                    "model": validated_state.get("model_name"),
                    "gem_id": validated_state.get("gem_id"),
                    "updated_at": snapshot.updated_at.isoformat(),
                    "schema_version": snapshot.schema_version,
                })

            return {
                "object": "list",
                "provider": self.provider.provider_name,
                "backend": "webapi",
                "count": len(data),
                "data": data,
            }
        except HTTPException:
            raise
        except StateIntegrityError as e:
            logger.error(f"Invalid Gemini WebAPI conversation snapshot: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Invalid conversation snapshot: {str(e)}") from e
        except Exception as e:
            logger.error(f"Error listing Gemini WebAPI conversations: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Error listing Gemini conversations: {str(e)}") from e

    async def delete_conversations(self) -> dict:
        gemini_client = self._get_available_gemini_client()

        registry = get_gemini_chat_registry()
        if not registry or not registry.repository:
            raise HTTPException(status_code=503, detail="Session registry is not initialized.")

        try:
            snapshots = await registry.list_conversation_snapshots(self.provider.provider_name)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error listing Gemini WebAPI conversations for bulk delete: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Error listing Gemini conversations: {str(e)}") from e

        results = []
        deleted_count = 0
        failed_count = 0
        skipped_active_count = 0

        for snapshot in snapshots:
            conversation_id = snapshot.conversation_id
            local_cleanup_started = False
            try:
                await registry.begin_delete_session(conversation_id)
            except ConversationInUseError as e:
                skipped_active_count += 1
                results.append({
                    "id": conversation_id,
                    "status": "skipped_active",
                    "deleted": False,
                    "error": str(e),
                })
                continue

            try:
                if snapshot.provider_name != self.provider.provider_name:
                    raise StateIntegrityError("Snapshot provider does not match registry provider.")
                if snapshot.schema_version != SNAPSHOT_SCHEMA_VERSION:
                    raise StateIntegrityError(f"Unsupported snapshot schema version: {snapshot.schema_version}")

                validated_state = self.provider.validate_session_recovery(
                    snapshot.session_state,
                    {"conversation_id": conversation_id},
                )
                metadata = validated_state.get("metadata")
                remote_cid = metadata[0]

                await gemini_client.client.delete_chat(remote_cid)
                local_cleanup_started = True
                await registry.complete_delete_session(conversation_id)

                deleted_count += 1
                results.append({
                    "id": conversation_id,
                    "status": "deleted",
                    "deleted": True,
                })
            except AuthError as e:
                if not local_cleanup_started:
                    await registry.abort_delete_session(conversation_id)
                raise HTTPException(
                    status_code=401,
                    detail="The provided conversation_id requires an authenticated Gemini session. Please sign in and try again.",
                    headers={"WWW-Authenticate": "Bearer"},
                ) from e
            except (GeminiTimeoutError, APIError) as e:
                if not local_cleanup_started:
                    await registry.abort_delete_session(conversation_id)
                failed_count += 1
                logger.error(f"Error deleting Gemini WebAPI conversation {conversation_id}: {e}", exc_info=True)
                results.append({
                    "id": conversation_id,
                    "status": "failed",
                    "deleted": False,
                    "error": "Gemini remote delete failed.",
                })
            except StateIntegrityError as e:
                if not local_cleanup_started:
                    await registry.abort_delete_session(conversation_id)
                failed_count += 1
                logger.error(f"Invalid Gemini WebAPI conversation snapshot {conversation_id}: {e}", exc_info=True)
                results.append({
                    "id": conversation_id,
                    "status": "failed",
                    "deleted": False,
                    "error": str(e),
                })
            except Exception as e:
                if not local_cleanup_started:
                    await registry.abort_delete_session(conversation_id)
                failed_count += 1
                logger.error(f"Error deleting Gemini WebAPI conversation {conversation_id}: {e}", exc_info=True)
                results.append({
                    "id": conversation_id,
                    "status": "failed",
                    "deleted": False,
                    "error": str(e),
                })

        return {
            "object": "conversation.bulk_delete",
            "provider": self.provider.provider_name,
            "backend": "webapi",
            "total": len(snapshots),
            "deleted_count": deleted_count,
            "failed_count": failed_count,
            "skipped_active_count": skipped_active_count,
            "results": results,
        }

    async def delete_conversation(self, conversation_id: str) -> dict:
        gemini_client = self._get_available_gemini_client()

        registry = get_gemini_chat_registry()
        if not registry or not registry.repository:
            raise HTTPException(status_code=503, detail="Session registry is not initialized.")

        try:
            await registry.begin_delete_session(conversation_id)
        except ConversationInUseError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

        local_cleanup_started = False
        try:
            snapshot = await registry.repository.get_snapshot(conversation_id)
            if snapshot is None:
                raise SnapshotNotFoundError(f"Conversation snapshot not found: {conversation_id}")

            if snapshot.provider_name != self.provider.provider_name:
                raise StateIntegrityError("Snapshot provider does not match registry provider.")
            if snapshot.schema_version != SNAPSHOT_SCHEMA_VERSION:
                raise StateIntegrityError(f"Unsupported snapshot schema version: {snapshot.schema_version}")

            validated_state = self.provider.validate_session_recovery(
                snapshot.session_state,
                {"conversation_id": conversation_id},
            )
            metadata = validated_state.get("metadata")
            remote_cid = metadata[0]

            await gemini_client.client.delete_chat(remote_cid)
            local_cleanup_started = True
            await registry.complete_delete_session(conversation_id)
            return {
                "id": conversation_id,
                "object": "conversation.deleted",
                "deleted": True,
                "provider": self.provider.provider_name,
                "backend": "webapi",
            }
        except SnapshotNotFoundError:
            if not local_cleanup_started:
                await registry.abort_delete_session(conversation_id)
            raise HTTPException(status_code=404, detail="The provided conversation_id was not found.")
        except HTTPException:
            if not local_cleanup_started:
                await registry.abort_delete_session(conversation_id)
            raise
        except AuthError as e:
            if not local_cleanup_started:
                await registry.abort_delete_session(conversation_id)
            raise HTTPException(
                status_code=401,
                detail="The provided conversation_id requires an authenticated Gemini session. Please sign in and try again.",
                headers={"WWW-Authenticate": "Bearer"},
            ) from e
        except GeminiTimeoutError as e:
            if not local_cleanup_started:
                await registry.abort_delete_session(conversation_id)
            raise HTTPException(status_code=503, detail=f"Gemini delete request timed out: {str(e)}") from e
        except APIError as e:
            if not local_cleanup_started:
                await registry.abort_delete_session(conversation_id)
            logger.error(f"Error deleting Gemini WebAPI conversation remotely: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Error deleting Gemini conversation: {str(e)}") from e
        except Exception as e:
            if not local_cleanup_started:
                await registry.abort_delete_session(conversation_id)
            logger.error(f"Error deleting Gemini WebAPI conversation: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Error deleting Gemini conversation: {str(e)}") from e

    async def chat_completions(self, request: OpenAIChatRequest, cid: str, is_new_conversation: bool, tools_prompt: str) -> Any:
        try:
            gemini_client = get_gemini_client()
        except GeminiClientNotInitializedError as e:
            raise HTTPException(status_code=503, detail=str(e))

        normalized = self._get_normalized_payload(request)
        request.messages = normalized.messages
        files = normalized.files or None
        cleanup_started = False

        async def cleanup_once() -> None:
            nonlocal cleanup_started
            if cleanup_started:
                return
            cleanup_started = True
            await cleanup_staged_files(normalized)

        # Check client authentication status
        account_status = getattr(gemini_client.client, "account_status", None)
        status_name = getattr(account_status, "name", "UNKNOWN") if account_status else "UNKNOWN"
        
        if status_name != "AVAILABLE":
            logger.warning(f"Gemini client account status is '{status_name}'.")
            if status_name == "UNAUTHENTICATED":
                raise HTTPException(
                    status_code=401,
                    detail="The provided conversation_id requires an authenticated Gemini session. Please sign in and try again.",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            raise HTTPException(
                status_code=401 if status_name == "UNKNOWN" else 503,
                detail=f"Gemini client is not ready (status: {status_name}).",
            )

        # 1. Retrieve stateful SessionManager from SessionRegistry
        registry = get_gemini_chat_registry()
        if not registry:
            await cleanup_once()
            raise HTTPException(status_code=503, detail="Session registry is not initialized.")
        
        try:
            session_manager = await registry.get_session(
                cid,
                self.provider,
                allow_create=is_new_conversation,
                model=request.model,
                gem=request.gem,
            )
        except SnapshotNotFoundError:
            await cleanup_once()
            raise HTTPException(
                status_code=404,
                detail="The provided conversation_id was not found.",
            )
        except SessionRecoveryError as e:
            await cleanup_once()
            raise HTTPException(status_code=409, detail=str(e)) from e

        is_stream = request.stream if request.stream is not None else False

        try:
            # 2. Progressive Streaming Path (only if no tools are used)
            if is_stream and not request.tools:
                async def sse_generator():
                    from app.utils.streaming import format_sse_chunk, get_done_chunk
                    try:
                        async for chunk in session_manager.get_streaming_response_stateful(
                            model=request.model,
                            messages=request.messages,
                            tools_prompt=tools_prompt,
                            files=files,
                            gem=request.gem
                        ):
                            if chunk.get("type") == "chunk" and chunk.get("text_delta"):
                                openai_chunk = convert_to_openai_format(
                                    chunk["text_delta"],
                                    request.model or "unknown",
                                    stream=True
                                )
                                openai_chunk["conversation_id"] = cid
                                openai_chunk["reused_conversation"] = chunk.get("is_reused", False)
                                yield await format_sse_chunk(openai_chunk)
                    except (asyncio.CancelledError, GeneratorExit):
                        raise
                    except Exception as e:
                        logger.error(f"Error in Gemini WebAPI progressive streaming: {e}", exc_info=True)
                        raise
                    else:
                        await registry.save_session_snapshot(cid, self.provider, session_manager)
                        yield await get_done_chunk()
                    finally:
                        await cleanup_once()

                return StreamingResponse(
                    sse_generator(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                    }
                )

            # 3. Buffered Path (for non-streaming or tool-calling)
            response, is_reused = await session_manager.get_response_stateful(
                model=request.model,
                messages=request.messages,
                tools_prompt=tools_prompt,
                files=files,
                gem=request.gem
            )
            await registry.save_session_snapshot(cid, self.provider, session_manager)
            
            # 4. Parse tool calls if necessary
            tool_call = parse_tool_call(response.text) if request.tools else None
            
            # 5. Normalize response to OpenAI format
            openai_response = convert_to_openai_format(
                response.text, 
                request.model or "unknown", 
                is_stream, 
                tool_call
            )
            
            # Inject stateful conversation identifiers
            openai_response["conversation_id"] = cid
            openai_response["reused_conversation"] = is_reused
            
            if is_stream:
                from app.utils.streaming import simulate_streaming_generator
                await cleanup_once()
                return StreamingResponse(
                    simulate_streaming_generator(openai_response), 
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                    }
                )
            await cleanup_once()
            return openai_response

        except HTTPException:
            await cleanup_once()
            raise
        except APIError as e:
            await cleanup_once()
            if not is_new_conversation and self._is_unrecoverable_conversation_error(e):
                raise HTTPException(
                    status_code=410,
                    detail="The provided conversation_id can no longer be recovered. Start a new conversation.",
                ) from e
            logger.error(f"Error in GeminiWebAPIAdapter.chat_completions: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Error processing Gemini chat completion: {str(e)}")
        except Exception as e:
            await cleanup_once()
            logger.error(f"Error in GeminiWebAPIAdapter.chat_completions: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Error processing Gemini chat completion: {str(e)}")

    def _is_unrecoverable_conversation_error(self, error: APIError) -> bool:
        error_code = (
            getattr(error, "code", None)
            or getattr(error, "error_code", None)
            or getattr(error, "status_code", None)
        )
        if error_code is not None:
            return str(error_code) in UNRECOVERABLE_CONVERSATION_ERROR_CODES
        return any(code in str(error) for code in UNRECOVERABLE_CONVERSATION_ERROR_CODES)

    async def close(self) -> None:
        pass

    def serialize_session_state(self, session: Any) -> dict:
        return json.loads(serialize_session_state(session))

    def deserialize_session_state(
        self,
        session_state: dict,
        client: Any,
        *,
        model: Optional[Any] = None,
        gem: Optional[Any] = None,
    ) -> Any:
        return deserialize_session_state(
            json.dumps(session_state),
            client,
            model=model,
            gem=gem,
        )

    def validate_session_recovery(self, session_state: dict, client_context: Optional[dict] = None) -> dict:
        validate_session_state_payload(session_state)
        return session_state
