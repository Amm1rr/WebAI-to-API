import pytest
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import call
from fastapi import HTTPException
from gemini_webapi.exceptions import APIError, AuthError
from app.services.providers.base_repository import ConversationSnapshot
from app.services.providers.exceptions import ConversationInUseError
from app.services.providers.gemini.provider import GeminiProvider
from app.services.providers.gemini.session_manager import SessionRegistry
from app.services.providers.gemini.webapi_response_builder import (
    build_choice_artifacts,
    build_webapi_chat_completion_response,
    build_webapi_streaming_artifact_chunk,
)

@pytest.fixture
def provider():
    return GeminiProvider()


def make_delete_snapshot(conversation_id="conv-delete", remote_cid="c_remote_delete"):
    return ConversationSnapshot(
        conversation_id=conversation_id,
        provider_name="gemini",
        session_state={
            "provider_state_version": 1,
            "metadata": [remote_cid, "r_delete", "rc_delete", None, None, None, None, None, None, "ctx"],
            "gem_id": None,
            "model_name": "gemini-3-flash",
        },
        schema_version=1,
        updated_at=datetime.now(timezone.utc),
    )


def make_delete_client(mocker, status_name="AVAILABLE"):
    delete_chat = mocker.AsyncMock()
    return SimpleNamespace(
        client=SimpleNamespace(
            account_status=SimpleNamespace(name=status_name),
            delete_chat=delete_chat,
        )
    )

def test_parse_tool_call_valid(provider):
    """Verify _parse_tool_call correctly extracts tool calls from text."""
    text = 'Sure, I will call the tool: {"tool_call": {"name": "get_weather", "arguments": {"location": "San Francisco"}}}'
    tool_call = provider._parse_tool_call(text)
    
    assert tool_call is not None
    assert tool_call["name"] == "get_weather"
    assert tool_call["arguments"] == {"location": "San Francisco"}

def test_parse_tool_call_invalid(provider):
    """Verify _parse_tool_call returns None when no valid tool call is found."""
    text = "Hello, how can I help you today?"
    tool_call = provider._parse_tool_call(text)
    
    assert tool_call is None

def test_convert_to_openai_format_non_streaming(provider):
    """Verify _convert_to_openai_format for non-streaming response."""
    response_text = "Hello world"
    model = "gemini-3-flash"
    
    result = provider._convert_to_openai_format(response_text, model, stream=False)
    
    assert result["model"] == model
    assert result["object"] == "chat.completion"
    assert result["choices"][0]["message"]["content"] == response_text
    assert result["choices"][0]["finish_reason"] == "stop"

def test_convert_to_openai_format_streaming(provider):
    """Verify _convert_to_openai_format for streaming chunk."""
    response_text = "Hello world"
    model = "gemini-3-flash"
    
    result = provider._convert_to_openai_format(response_text, model, stream=True)
    
    assert result["model"] == model
    assert result["object"] == "chat.completion.chunk"
    assert result["choices"][0]["delta"]["content"] == response_text

def test_convert_to_openai_format_with_tool_call(provider):
    """Verify _convert_to_openai_format when a tool call is present."""
    response_text = '{"tool_call": {"name": "test"}}'
    tool_call = {"name": "test_tool", "arguments": {"arg": 1}}
    model = "gemini-3-flash"
    
    result = provider._convert_to_openai_format(response_text, model, stream=False, tool_call=tool_call)
    
    assert result["choices"][0]["finish_reason"] == "tool_calls"
    assert result["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "test_tool"
    assert json.loads(result["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]) == {"arg": 1}


def test_build_choice_artifacts_maps_safe_webapi_metadata():
    response = SimpleNamespace(
        images=[
            SimpleNamespace(
                url="https://example.com/image.png",
                title="Generated image",
                alt="A generated image",
            )
        ],
        videos=[
            SimpleNamespace(
                url="https://example.com/video.mp4",
                title="Generated video",
                thumbnail="https://example.com/video-thumb.jpg",
            )
        ],
        media=[
            SimpleNamespace(
                mp3_url="https://example.com/audio.mp3",
                mp3_thumbnail="https://example.com/audio-thumb.jpg",
                title="Generated audio",
            )
        ],
    )

    artifacts = build_choice_artifacts(response)

    assert artifacts == [
        {
            "type": "image",
            "provider": "gemini_webapi",
            "title": "Generated image",
            "url": "https://example.com/image.png",
            "alt": "A generated image",
        },
        {
            "type": "video",
            "provider": "gemini_webapi",
            "title": "Generated video",
            "url": "https://example.com/video.mp4",
            "thumbnail_url": "https://example.com/video-thumb.jpg",
        },
        {
            "type": "audio",
            "provider": "gemini_webapi",
            "title": "Generated audio",
            "url": "https://example.com/audio.mp3",
            "thumbnail_url": "https://example.com/audio-thumb.jpg",
        },
    ]


def test_build_choice_artifacts_ignores_missing_and_non_string_fields():
    response = SimpleNamespace(
        images=[
            SimpleNamespace(url=123, title=None, alt=""),
            SimpleNamespace(url="https://example.com/image.png", unknown_field="ignored"),
        ],
        videos=[
            SimpleNamespace(url=None, title=456, thumbnail={}),
        ],
        media=[
            SimpleNamespace(mp3_url="", mp3_thumbnail=None, title=None),
        ],
    )

    artifacts = build_choice_artifacts(response)

    assert artifacts == [
        {
            "type": "image",
            "provider": "gemini_webapi",
            "url": "https://example.com/image.png",
        }
    ]


def test_build_webapi_chat_completion_response_keeps_text_only_shape():
    response = SimpleNamespace(text="Hello world", images=[], videos=[], media=[])

    result = build_webapi_chat_completion_response(
        response,
        "gemini-3-flash",
        conversation_id="conv-1",
        reused_conversation=False,
    )

    assert result["model"] == "gemini-3-flash"
    assert result["choices"][0]["message"]["content"] == "Hello world"
    assert "artifacts" not in result["choices"][0]
    assert result["conversation_id"] == "conv-1"
    assert result["reused_conversation"] is False


def test_build_webapi_chat_completion_response_attaches_artifacts_without_thoughts():
    response = SimpleNamespace(
        text="Done.",
        thoughts="internal reasoning",
        images=[
            SimpleNamespace(
                url="https://example.com/image.png",
                title="Generated image",
            )
        ],
        videos=[],
        media=[],
    )

    result = build_webapi_chat_completion_response(
        response,
        "gemini-3-flash",
        conversation_id="conv-2",
        reused_conversation=True,
    )

    assert result["choices"][0]["message"]["content"] == "Done."
    assert result["choices"][0]["artifacts"] == [
        {
            "type": "image",
            "provider": "gemini_webapi",
            "title": "Generated image",
            "url": "https://example.com/image.png",
        }
    ]
    assert "thoughts" not in result["choices"][0]
    assert result["conversation_id"] == "conv-2"
    assert result["reused_conversation"] is True


def test_build_webapi_chat_completion_response_preserves_tool_calls_and_artifacts():
    response = SimpleNamespace(
        text='{"tool_call": {"name": "generate_report", "arguments": {"topic": "status"}}}',
        thoughts="internal reasoning",
        images=[
            SimpleNamespace(
                url="https://example.com/report.png",
                title="Report image",
            )
        ],
        videos=[],
        media=[],
    )

    result = build_webapi_chat_completion_response(
        response,
        "gemini-3-flash",
        tool_call={"name": "generate_report", "arguments": {"topic": "status"}},
        conversation_id="conv-3",
        reused_conversation=False,
    )

    message = result["choices"][0]["message"]
    assert message["content"] is None
    assert message["tool_calls"]
    assert result["choices"][0]["artifacts"] == [
        {
            "type": "image",
            "provider": "gemini_webapi",
            "title": "Report image",
            "url": "https://example.com/report.png",
        }
    ]
    assert "thoughts" not in result["choices"][0]
    assert result["conversation_id"] == "conv-3"


def test_build_webapi_streaming_artifact_chunk_behaviour():
    empty_response = SimpleNamespace(text="Done.", images=[], videos=[], media=[], thoughts="hidden")
    assert build_webapi_streaming_artifact_chunk(
        empty_response,
        "gemini-3-flash",
        conversation_id="conv-4",
        reused_conversation=False,
    ) is None

    response = SimpleNamespace(
        text="Done.",
        images=[
            SimpleNamespace(
                url="https://example.com/image.png",
                title="Generated image",
                alt="A generated image",
            )
        ],
        videos=[],
        media=[],
        thoughts="hidden",
    )

    result = build_webapi_streaming_artifact_chunk(
        response,
        "gemini-3-flash",
        conversation_id="conv-4",
        reused_conversation=True,
    )

    assert result["choices"][0]["delta"] == {}
    assert result["choices"][0]["artifacts"] == [
        {
            "type": "image",
            "provider": "gemini_webapi",
            "title": "Generated image",
            "url": "https://example.com/image.png",
            "alt": "A generated image",
        }
    ]
    assert "thoughts" not in result["choices"][0]
    assert result["conversation_id"] == "conv-4"
    assert result["reused_conversation"] is True


@pytest.mark.asyncio
async def test_delete_conversation_success(mocker, provider):
    snapshot = make_delete_snapshot()
    gemini_client = make_delete_client(mocker)
    mock_registry = mocker.Mock()
    mock_registry.repository.get_snapshot = mocker.AsyncMock(return_value=snapshot)
    mock_registry.begin_delete_session = mocker.AsyncMock()
    mock_registry.complete_delete_session = mocker.AsyncMock()
    mock_registry.abort_delete_session = mocker.AsyncMock()

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=gemini_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)

    result = await provider.delete_conversation("conv-delete")

    assert result == {
        "id": "conv-delete",
        "object": "conversation.deleted",
        "deleted": True,
        "provider": "gemini",
        "backend": "webapi",
    }
    mock_registry.begin_delete_session.assert_called_once_with("conv-delete")
    gemini_client.client.delete_chat.assert_called_once_with("c_remote_delete")
    mock_registry.complete_delete_session.assert_called_once_with("conv-delete")
    mock_registry.abort_delete_session.assert_not_called()


@pytest.mark.asyncio
async def test_delete_conversation_missing_snapshot_returns_404(mocker, provider):
    gemini_client = make_delete_client(mocker)
    mock_registry = mocker.Mock()
    mock_registry.repository.get_snapshot = mocker.AsyncMock(return_value=None)
    mock_registry.begin_delete_session = mocker.AsyncMock()
    mock_registry.complete_delete_session = mocker.AsyncMock()
    mock_registry.abort_delete_session = mocker.AsyncMock()

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=gemini_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)

    with pytest.raises(HTTPException) as exc_info:
        await provider.delete_conversation("missing-conv")

    assert exc_info.value.status_code == 404
    mock_registry.abort_delete_session.assert_called_once_with("missing-conv")
    gemini_client.client.delete_chat.assert_not_called()
    mock_registry.complete_delete_session.assert_not_called()


@pytest.mark.asyncio
async def test_delete_conversation_active_session_returns_409(mocker, provider):
    gemini_client = make_delete_client(mocker)
    mock_registry = mocker.Mock()
    mock_registry.repository = mocker.Mock()
    mock_registry.begin_delete_session = mocker.AsyncMock(
        side_effect=ConversationInUseError("Conversation is currently in use: conv-active")
    )

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=gemini_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)

    with pytest.raises(HTTPException) as exc_info:
        await provider.delete_conversation("conv-active")

    assert exc_info.value.status_code == 409
    gemini_client.client.delete_chat.assert_not_called()


@pytest.mark.asyncio
async def test_delete_conversation_unauthenticated_returns_401(mocker, provider):
    gemini_client = make_delete_client(mocker, status_name="UNAUTHENTICATED")
    mock_registry = mocker.Mock()
    mock_registry.begin_delete_session = mocker.AsyncMock()

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=gemini_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)

    with pytest.raises(HTTPException) as exc_info:
        await provider.delete_conversation("conv-delete")

    assert exc_info.value.status_code == 401
    mock_registry.begin_delete_session.assert_not_called()


@pytest.mark.asyncio
async def test_delete_conversation_gemini_api_error_returns_500_and_aborts(mocker, provider):
    snapshot = make_delete_snapshot()
    gemini_client = make_delete_client(mocker)
    gemini_client.client.delete_chat.side_effect = APIError("Batch execution failed with status code 500")
    mock_registry = mocker.Mock()
    mock_registry.repository.get_snapshot = mocker.AsyncMock(return_value=snapshot)
    mock_registry.begin_delete_session = mocker.AsyncMock()
    mock_registry.complete_delete_session = mocker.AsyncMock()
    mock_registry.abort_delete_session = mocker.AsyncMock()

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=gemini_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)

    with pytest.raises(HTTPException) as exc_info:
        await provider.delete_conversation("conv-delete")

    assert exc_info.value.status_code == 500
    mock_registry.abort_delete_session.assert_called_once_with("conv-delete")
    mock_registry.complete_delete_session.assert_not_called()


@pytest.mark.asyncio
async def test_delete_conversation_repository_failure_returns_500_and_clears_tombstone(mocker, provider):
    snapshot = make_delete_snapshot()
    gemini_client = make_delete_client(mocker)
    repository = mocker.Mock()
    repository.get_snapshot = mocker.AsyncMock(return_value=snapshot)
    repository.delete_snapshot = mocker.AsyncMock(side_effect=RuntimeError("sqlite unavailable"))
    registry = SessionRegistry(gemini_client, repository=repository)
    manager = await registry.get_session("conv-delete")

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=gemini_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=registry)

    with pytest.raises(HTTPException) as exc_info:
        await provider.delete_conversation("conv-delete")

    assert exc_info.value.status_code == 500
    assert "conv-delete" not in registry._deleting
    assert registry._sessions["conv-delete"] is manager
    gemini_client.client.delete_chat.assert_called_once_with("c_remote_delete")
    repository.delete_snapshot.assert_called_once_with("conv-delete")


@pytest.mark.asyncio
async def test_list_conversations_returns_persisted_conversation_fields(mocker, provider):
    updated_at = datetime(2026, 6, 2, 12, 30, tzinfo=timezone.utc)
    snapshot = ConversationSnapshot(
        conversation_id="conv-list",
        provider_name="gemini",
        session_state={
            "provider_state_version": 1,
            "metadata": ["remote-cid-secret", "rid", "rcid", None, None, None, None, None, None, "ctx"],
            "gem_id": "gem-123",
            "model_name": "gemini-3-flash",
        },
        schema_version=1,
        updated_at=updated_at,
    )
    mock_registry = mocker.Mock()
    mock_registry.repository = mocker.Mock()
    mock_registry.list_conversation_snapshots = mocker.AsyncMock(return_value=[snapshot])

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)
    get_client = mocker.patch(
        "app.services.providers.gemini.webapi_adapter.get_gemini_client",
        side_effect=AssertionError("list_conversations must not call Gemini remote client"),
    )
    deserialize = mocker.patch.object(
        provider,
        "deserialize_session_state",
        side_effect=AssertionError("list_conversations must not restore ChatSession"),
    )

    result = await provider.list_conversations()

    assert result == {
        "object": "list",
        "provider": "gemini",
        "backend": "webapi",
        "count": 1,
        "data": [
            {
                "id": "conv-list",
                "object": "conversation",
                "provider": "gemini",
                "backend": "webapi",
                "model": "gemini-3-flash",
                "gem_id": "gem-123",
                "updated_at": updated_at.isoformat(),
                "schema_version": 1,
            }
        ],
    }
    assert "metadata" not in result["data"][0]
    assert "remote-cid-secret" not in json.dumps(result)
    mock_registry.list_conversation_snapshots.assert_called_once_with("gemini")
    get_client.assert_not_called()
    deserialize.assert_not_called()


@pytest.mark.asyncio
async def test_list_conversations_corrupt_snapshot_returns_500(mocker, provider):
    snapshot = ConversationSnapshot(
        conversation_id="conv-corrupt",
        provider_name="gemini",
        session_state={
            "provider_state_version": 1,
            "metadata": ["remote-cid-only"],
            "gem_id": None,
            "model_name": "gemini-3-flash",
        },
        schema_version=1,
        updated_at=datetime.now(timezone.utc),
    )
    mock_registry = mocker.Mock()
    mock_registry.repository = mocker.Mock()
    mock_registry.list_conversation_snapshots = mocker.AsyncMock(return_value=[snapshot])

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)

    with pytest.raises(HTTPException) as exc_info:
        await provider.list_conversations()

    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_list_conversations_registry_unavailable_returns_503(mocker, provider):
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=None)

    with pytest.raises(HTTPException) as exc_info:
        await provider.list_conversations()

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_delete_conversations_empty_list_returns_report(mocker, provider):
    gemini_client = make_delete_client(mocker)
    mock_registry = mocker.Mock()
    mock_registry.repository = mocker.Mock()
    mock_registry.list_conversation_snapshots = mocker.AsyncMock(return_value=[])

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=gemini_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)

    result = await provider.delete_conversations()

    assert result == {
        "object": "conversation.bulk_delete",
        "provider": "gemini",
        "backend": "webapi",
        "total": 0,
        "deleted_count": 0,
        "failed_count": 0,
        "skipped_active_count": 0,
        "results": [],
    }
    gemini_client.client.delete_chat.assert_not_called()


@pytest.mark.asyncio
async def test_delete_conversations_success_deletes_all_inactive_snapshots(mocker, provider):
    snapshots = [
        make_delete_snapshot("conv-a", "remote-a"),
        make_delete_snapshot("conv-b", "remote-b"),
    ]
    gemini_client = make_delete_client(mocker)
    mock_registry = mocker.Mock()
    mock_registry.repository = SimpleNamespace(delete_snapshot=mocker.AsyncMock())
    mock_registry.list_conversation_snapshots = mocker.AsyncMock(return_value=snapshots)
    mock_registry.begin_delete_session = mocker.AsyncMock()
    mock_registry.complete_delete_session = mocker.AsyncMock()
    mock_registry.abort_delete_session = mocker.AsyncMock()

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=gemini_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)

    result = await provider.delete_conversations()

    assert result["total"] == 2
    assert result["deleted_count"] == 2
    assert result["failed_count"] == 0
    assert result["skipped_active_count"] == 0
    assert result["results"] == [
        {"id": "conv-a", "status": "deleted", "deleted": True},
        {"id": "conv-b", "status": "deleted", "deleted": True},
    ]
    gemini_client.client.delete_chat.assert_has_awaits([
        call("remote-a"),
        call("remote-b"),
    ])
    mock_registry.complete_delete_session.assert_has_awaits([
        call("conv-a"),
        call("conv-b"),
    ])
    assert "remote-a" not in json.dumps(result)
    assert "remote-b" not in json.dumps(result)


@pytest.mark.asyncio
async def test_delete_conversations_success_completes_snapshot_deletion(mocker, provider):
    snapshots = [
        make_delete_snapshot("conv-a", "remote-a"),
        make_delete_snapshot("conv-b", "remote-b"),
    ]
    gemini_client = make_delete_client(mocker)
    mock_registry = mocker.Mock()
    mock_registry.repository = SimpleNamespace(delete_snapshot=mocker.AsyncMock())
    mock_registry.list_conversation_snapshots = mocker.AsyncMock(return_value=snapshots)
    mock_registry.begin_delete_session = mocker.AsyncMock()
    mock_registry.complete_delete_session = mocker.AsyncMock()
    mock_registry.abort_delete_session = mocker.AsyncMock()

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=gemini_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)

    result = await provider.delete_conversations()

    assert result["deleted_count"] == 2
    assert result["failed_count"] == 0
    assert result["skipped_active_count"] == 0
    assert result["results"] == [
        {"id": "conv-a", "status": "deleted", "deleted": True},
        {"id": "conv-b", "status": "deleted", "deleted": True},
    ]
    assert mock_registry.complete_delete_session.await_args_list == [
        call("conv-a"),
        call("conv-b"),
    ]


@pytest.mark.asyncio
async def test_delete_conversations_skips_active_and_continues(mocker, provider):
    snapshots = [
        make_delete_snapshot("conv-active", "remote-active"),
        make_delete_snapshot("conv-ok", "remote-ok"),
    ]
    gemini_client = make_delete_client(mocker)
    mock_registry = mocker.Mock()
    mock_registry.repository = mocker.Mock()
    mock_registry.list_conversation_snapshots = mocker.AsyncMock(return_value=snapshots)
    mock_registry.begin_delete_session = mocker.AsyncMock(side_effect=[
        ConversationInUseError("Conversation is currently in use"),
        None,
    ])
    mock_registry.complete_delete_session = mocker.AsyncMock()
    mock_registry.abort_delete_session = mocker.AsyncMock()

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=gemini_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)

    result = await provider.delete_conversations()

    assert result["deleted_count"] == 1
    assert result["failed_count"] == 0
    assert result["skipped_active_count"] == 1
    assert result["results"][0]["status"] == "skipped_active"
    assert result["results"][0]["deleted"] is False
    assert result["results"][1] == {"id": "conv-ok", "status": "deleted", "deleted": True}
    gemini_client.client.delete_chat.assert_awaited_once_with("remote-ok")
    mock_registry.abort_delete_session.assert_not_called()


@pytest.mark.asyncio
async def test_delete_conversations_remote_api_error_records_failed_and_continues(mocker, provider):
    snapshots = [
        make_delete_snapshot("conv-fail", "remote-fail"),
        make_delete_snapshot("conv-ok", "remote-ok"),
    ]
    gemini_client = make_delete_client(mocker)

    async def delete_chat(remote_cid):
        if remote_cid == "remote-fail":
            raise APIError("remote failed")

    gemini_client.client.delete_chat = mocker.AsyncMock(side_effect=delete_chat)
    mock_registry = mocker.Mock()
    mock_registry.repository = mocker.Mock()
    mock_registry.list_conversation_snapshots = mocker.AsyncMock(return_value=snapshots)
    mock_registry.begin_delete_session = mocker.AsyncMock()
    mock_registry.complete_delete_session = mocker.AsyncMock()
    mock_registry.abort_delete_session = mocker.AsyncMock()

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=gemini_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)

    result = await provider.delete_conversations()

    assert result["deleted_count"] == 1
    assert result["failed_count"] == 1
    assert result["skipped_active_count"] == 0
    assert result["results"][0]["id"] == "conv-fail"
    assert result["results"][0]["status"] == "failed"
    assert result["results"][0]["deleted"] is False
    assert result["results"][1] == {"id": "conv-ok", "status": "deleted", "deleted": True}
    mock_registry.abort_delete_session.assert_called_once_with("conv-fail")
    mock_registry.complete_delete_session.assert_called_once_with("conv-ok")
    assert "remote-fail" not in json.dumps(result)


@pytest.mark.asyncio
async def test_delete_conversations_auth_error_aborts_bulk_run(mocker, provider):
    snapshots = [
        make_delete_snapshot("conv-auth", "remote-auth"),
        make_delete_snapshot("conv-later", "remote-later"),
    ]
    gemini_client = make_delete_client(mocker)
    gemini_client.client.delete_chat.side_effect = AuthError("auth expired")
    mock_registry = mocker.Mock()
    mock_registry.repository = mocker.Mock()
    mock_registry.list_conversation_snapshots = mocker.AsyncMock(return_value=snapshots)
    mock_registry.begin_delete_session = mocker.AsyncMock()
    mock_registry.complete_delete_session = mocker.AsyncMock()
    mock_registry.abort_delete_session = mocker.AsyncMock()

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=gemini_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)

    with pytest.raises(HTTPException) as exc_info:
        await provider.delete_conversations()

    assert exc_info.value.status_code == 401
    assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}
    gemini_client.client.delete_chat.assert_awaited_once_with("remote-auth")
    mock_registry.abort_delete_session.assert_called_once_with("conv-auth")
    mock_registry.complete_delete_session.assert_not_called()
    mock_registry.begin_delete_session.assert_awaited_once_with("conv-auth")


@pytest.mark.asyncio
async def test_delete_conversations_local_cleanup_failure_records_failed_and_clears_tombstone(mocker, provider):
    snapshot = make_delete_snapshot("conv-cleanup", "remote-cleanup")
    gemini_client = make_delete_client(mocker)
    repository = mocker.Mock()
    repository.list_snapshots = mocker.AsyncMock(return_value=[snapshot])
    repository.delete_snapshot = mocker.AsyncMock(side_effect=RuntimeError("sqlite unavailable"))
    registry = SessionRegistry(gemini_client, repository=repository)

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=gemini_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=registry)

    result = await provider.delete_conversations()

    assert result["deleted_count"] == 0
    assert result["failed_count"] == 1
    assert result["results"][0]["id"] == "conv-cleanup"
    assert result["results"][0]["status"] == "failed"
    assert "conv-cleanup" not in registry._deleting
    gemini_client.client.delete_chat.assert_called_once_with("remote-cleanup")
    repository.delete_snapshot.assert_called_once_with("conv-cleanup")


@pytest.mark.asyncio
async def test_delete_conversations_tombstone_cleared_after_remote_failure(mocker, provider):
    snapshot = make_delete_snapshot("conv-remote-fail", "remote-fail")
    gemini_client = make_delete_client(mocker)
    gemini_client.client.delete_chat.side_effect = APIError("remote failed")
    repository = mocker.Mock()
    repository.list_snapshots = mocker.AsyncMock(return_value=[snapshot])
    repository.delete_snapshot = mocker.AsyncMock()
    registry = SessionRegistry(gemini_client, repository=repository)

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=gemini_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=registry)

    result = await provider.delete_conversations()

    assert result["failed_count"] == 1
    assert "conv-remote-fail" not in registry._deleting
    repository.delete_snapshot.assert_not_called()


@pytest.mark.asyncio
async def test_delete_conversations_listing_failure_returns_500(mocker, provider):
    gemini_client = make_delete_client(mocker)
    mock_registry = mocker.Mock()
    mock_registry.repository = mocker.Mock()
    mock_registry.list_conversation_snapshots = mocker.AsyncMock(side_effect=RuntimeError("sqlite unavailable"))

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=gemini_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)

    with pytest.raises(HTTPException) as exc_info:
        await provider.delete_conversations()

    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_delete_conversations_unauthenticated_returns_same_status_as_single_delete(mocker, provider):
    gemini_client = make_delete_client(mocker, status_name="UNAUTHENTICATED")
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=gemini_client)

    with pytest.raises(HTTPException) as exc_info:
        await provider.delete_conversations()

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_chat_completions_stateful_buffered(mocker, provider):
    """Verify chat_completions retrieves SessionManager and executes stateful buffered response."""
    from app.schemas.request import OpenAIChatRequest
    from app.services.providers.gemini.session_manager import SessionManager, SessionRegistry
    
    mock_client = mocker.Mock()
    mock_client.client.account_status.name = "AVAILABLE"
    mock_registry = mocker.Mock(spec=SessionRegistry)
    mock_manager = mocker.Mock(spec=SessionManager)
    
    # Mock return values for registry and manager
    mock_response = mocker.Mock()
    mock_response.text = "Stateful response content"
    
    mock_manager.get_response_stateful = mocker.AsyncMock(return_value=(mock_response, True))
    mock_registry.get_session = mocker.AsyncMock(return_value=mock_manager)
    mock_registry.save_session_snapshot = mocker.AsyncMock()
    
    # Mock global client and session registry resolution
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=mock_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)
    
    request = OpenAIChatRequest(
        messages=[{"role": "user", "content": "I am Ali. What is my name?"}],
        model="gemini-3-flash",
        conversation_id="test_token_XYZ"
    )
    
    result = await provider.chat_completions(request)
    
    assert result["conversation_id"] == "test_token_XYZ"
    assert result["reused_conversation"] is True
    assert result["choices"][0]["message"]["content"] == "Stateful response content"
    mock_registry.get_session.assert_called_once_with(
        "test_token_XYZ",
        provider,
        allow_create=False,
        model="gemini-3-flash",
        gem=None,
    )
    mock_manager.get_response_stateful.assert_called_once()
    mock_registry.save_session_snapshot.assert_called_once_with("test_token_XYZ", provider, mock_manager)


@pytest.mark.asyncio
async def test_chat_completions_invalid_model_buffered_returns_400_before_session_use(mocker, provider):
    from app.schemas.request import OpenAIChatRequest
    from app.services.providers.gemini.session_manager import SessionRegistry

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=mocker.Mock())
    mock_registry = mocker.Mock(spec=SessionRegistry)
    mock_registry.get_session = mocker.AsyncMock()
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)

    request = OpenAIChatRequest(
        messages=[{"role": "user", "content": "What is my name?"}],
        model="gemini-3",
        stream=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        await provider.chat_completions(request)

    assert exc_info.value.status_code == 400
    assert "Unknown model name: gemini-3" in exc_info.value.detail
    mock_registry.get_session.assert_not_called()


@pytest.mark.asyncio
async def test_chat_completions_invalid_model_streaming_returns_400_before_stream(mocker, provider):
    from app.schemas.request import OpenAIChatRequest
    from app.services.providers.gemini.session_manager import SessionRegistry

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=mocker.Mock())
    mock_registry = mocker.Mock(spec=SessionRegistry)
    mock_registry.get_session = mocker.AsyncMock()
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)

    request = OpenAIChatRequest(
        messages=[{"role": "user", "content": "What is my name?"}],
        model="gemini-3",
        stream=True,
    )

    with pytest.raises(HTTPException) as exc_info:
        await provider.chat_completions(request)

    assert exc_info.value.status_code == 400
    assert "Unknown model name: gemini-3" in exc_info.value.detail
    mock_registry.get_session.assert_not_called()


@pytest.mark.asyncio
async def test_chat_completions_with_conversation_id_requires_authenticated_client(mocker, provider):
    from app.schemas.request import OpenAIChatRequest
    from app.services.providers.gemini.session_manager import SessionRegistry

    mock_client = mocker.Mock()
    mock_client.client.account_status.name = "UNAUTHENTICATED"
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=mock_client)

    mock_registry = mocker.Mock(spec=SessionRegistry)
    mock_registry.get_session = mocker.AsyncMock()
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)

    request = OpenAIChatRequest(
        messages=[{"role": "user", "content": "What is my name?"}],
        model="gemini-3-flash",
        conversation_id="existing-conversation",
        stream=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        await provider.chat_completions(request)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == (
        "The provided conversation_id requires an authenticated Gemini session. "
        "Please sign in and try again."
    )
    assert exc_info.value.headers["WWW-Authenticate"] == "Bearer"
    mock_registry.get_session.assert_not_called()


@pytest.mark.asyncio
async def test_chat_completions_with_conversation_id_fails_closed_when_auth_status_unknown(mocker, provider):
    from app.schemas.request import OpenAIChatRequest
    from app.services.providers.gemini.session_manager import SessionRegistry

    mock_client = SimpleNamespace(
        client=SimpleNamespace(account_status=SimpleNamespace())
    )
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=mock_client)

    mock_registry = mocker.Mock(spec=SessionRegistry)
    mock_registry.get_session = mocker.AsyncMock()
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)

    request = OpenAIChatRequest(
        messages=[{"role": "user", "content": "What is my name?"}],
        model="gemini-3-flash",
        conversation_id="existing-conversation",
        stream=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        await provider.chat_completions(request)

    assert exc_info.value.status_code == 401
    mock_registry.get_session.assert_not_called()


@pytest.mark.asyncio
async def test_chat_completions_with_stale_conversation_id_returns_410(mocker, provider):
    from app.schemas.request import OpenAIChatRequest
    from app.services.providers.gemini.session_manager import SessionManager, SessionRegistry

    mock_client = mocker.Mock()
    mock_client.client.account_status.name = "AVAILABLE"
    mock_registry = mocker.Mock(spec=SessionRegistry)
    mock_manager = mocker.Mock(spec=SessionManager)
    mock_manager.get_response_stateful = mocker.AsyncMock(
        side_effect=APIError(
            "Failed to generate contents (stream). Unknown API error code: 1097. "
            "This might be a temporary Google service issue."
        )
    )
    mock_registry.get_session = mocker.AsyncMock(return_value=mock_manager)
    mock_registry.save_session_snapshot = mocker.AsyncMock()

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=mock_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)

    request = OpenAIChatRequest(
        messages=[{"role": "user", "content": "What is my name?"}],
        model="gemini-3-flash",
        conversation_id="stale-conversation",
        stream=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        await provider.chat_completions(request)

    assert exc_info.value.status_code == 410
    assert exc_info.value.detail == (
        "The provided conversation_id can no longer be recovered. Start a new conversation."
    )
    mock_registry.save_session_snapshot.assert_not_called()


@pytest.mark.asyncio
async def test_chat_completions_new_prompt_does_not_map_api_1097_to_recovery_error(mocker, provider):
    from app.schemas.request import OpenAIChatRequest
    from app.services.providers.gemini.session_manager import SessionManager, SessionRegistry

    mock_client = mocker.Mock()
    mock_client.client.account_status.name = "AVAILABLE"
    mock_registry = mocker.Mock(spec=SessionRegistry)
    mock_manager = mocker.Mock(spec=SessionManager)
    mock_manager.get_response_stateful = mocker.AsyncMock(
        side_effect=APIError("Unknown API error code: 1097")
    )
    mock_registry.get_session = mocker.AsyncMock(return_value=mock_manager)
    mock_registry.save_session_snapshot = mocker.AsyncMock()

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=mock_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)
    mocker.patch("app.services.providers.gemini.provider.generate_opaque_token", return_value="new-conversation")

    request = OpenAIChatRequest(
        messages=[{"role": "user", "content": "Hello"}],
        model="gemini-3-flash",
        stream=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        await provider.chat_completions(request)

    assert exc_info.value.status_code == 500
    assert "Unknown API error code: 1097" in exc_info.value.detail
    mock_registry.get_session.assert_called_once_with(
        "new-conversation",
        provider,
        allow_create=True,
        model="gemini-3-flash",
        gem=None,
    )
    mock_registry.save_session_snapshot.assert_not_called()


@pytest.mark.asyncio
async def test_chat_completions_stateful_streaming(mocker, provider):
    """Verify chat_completions retrieves SessionManager and executes stateful streaming response with SSE format."""
    from app.schemas.request import OpenAIChatRequest
    from app.services.providers.gemini.session_manager import SessionManager, SessionRegistry
    
    mock_client = mocker.Mock()
    mock_client.client.account_status.name = "AVAILABLE"
    mock_registry = mocker.Mock(spec=SessionRegistry)
    mock_manager = mocker.Mock(spec=SessionManager)
    
    # Mock generator yielding chunks in our defined stateful format
    async def mock_generator(*args, **kwargs):
        yield {
            "type": "chunk",
            "text_delta": "Stateful delta content",
            "is_reused": True
        }
        yield {
            "type": "final",
            "response": SimpleNamespace(
                text="Stateful response content",
                images=[],
                videos=[],
                media=[],
                thoughts="hidden",
            ),
            "is_reused": True,
        }

    mock_manager.get_streaming_response_stateful = mock_generator
    mock_registry.get_session = mocker.AsyncMock(return_value=mock_manager)
    mock_registry.save_session_snapshot = mocker.AsyncMock()
    
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=mock_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)
    
    request = OpenAIChatRequest(
        messages=[{"role": "user", "content": "I am Ali. What is my name?"}],
        model="gemini-3-flash",
        stream=True,
        conversation_id="test_token_XYZ"
    )
    
    response = await provider.chat_completions(request)
    assert response is not None
    
    # Verify it is indeed a StreamingResponse
    from fastapi.responses import StreamingResponse
    assert isinstance(response, StreamingResponse)
    
    # Consume the SSE generator and verify chunk parsing, conversation_id and reused_conversation keys
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
        
    assert len(chunks) == 2
    assert chunks[0].startswith("data: ")
    assert chunks[1] == "data: [DONE]\n\n"
    
    chunk_data = json.loads(chunks[0][6:-2])
    assert chunk_data["choices"][0]["delta"]["content"] == "Stateful delta content"
    assert chunk_data["conversation_id"] == "test_token_XYZ"
    assert chunk_data["reused_conversation"] is True
    mock_registry.save_session_snapshot.assert_called_once_with("test_token_XYZ", provider, mock_manager)


@pytest.mark.asyncio
async def test_chat_completions_stateful_streaming_emits_final_artifact_chunk_before_done(mocker, provider):
    """Verify WebAPI streaming emits a final artifact chunk before [DONE] when artifacts exist."""
    from app.schemas.request import OpenAIChatRequest
    from app.services.providers.gemini.session_manager import SessionManager, SessionRegistry

    mock_client = mocker.Mock()
    mock_client.client.account_status.name = "AVAILABLE"
    mock_registry = mocker.Mock(spec=SessionRegistry)
    mock_manager = mocker.Mock(spec=SessionManager)

    async def mock_generator(*args, **kwargs):
        yield {
            "type": "chunk",
            "text_delta": "Stateful delta content",
            "is_reused": False,
        }
        yield {
            "type": "final",
            "response": SimpleNamespace(
                text="Stateful response content",
                images=[
                    SimpleNamespace(
                        url="https://example.com/generated.png",
                        title="Generated image",
                        alt="A generated image",
                    )
                ],
                videos=[],
                media=[],
                thoughts="hidden",
            ),
            "is_reused": False,
        }

    mock_manager.get_streaming_response_stateful = mock_generator
    mock_registry.get_session = mocker.AsyncMock(return_value=mock_manager)
    mock_registry.save_session_snapshot = mocker.AsyncMock()

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=mock_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)

    request = OpenAIChatRequest(
        messages=[{"role": "user", "content": "I am Ali. What is my name?"}],
        model="gemini-3-flash",
        stream=True,
        conversation_id="test_token_XYZ",
    )

    response = await provider.chat_completions(request)
    assert response is not None

    from fastapi.responses import StreamingResponse
    assert isinstance(response, StreamingResponse)

    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)

    assert len(chunks) == 3

    text_chunk = json.loads(chunks[0][6:-2])
    artifact_chunk = json.loads(chunks[1][6:-2])

    assert text_chunk["choices"][0]["delta"]["content"] == "Stateful delta content"
    assert text_chunk["conversation_id"] == "test_token_XYZ"
    assert text_chunk["reused_conversation"] is False

    assert artifact_chunk["choices"][0]["delta"] == {}
    assert artifact_chunk["choices"][0]["finish_reason"] == "stop"
    assert artifact_chunk["choices"][0]["artifacts"] == [
        {
            "type": "image",
            "provider": "gemini_webapi",
            "title": "Generated image",
            "url": "https://example.com/generated.png",
            "alt": "A generated image",
        }
    ]
    assert "thoughts" not in artifact_chunk["choices"][0]
    assert artifact_chunk["conversation_id"] == "test_token_XYZ"
    assert artifact_chunk["reused_conversation"] is False
    assert chunks[2] == "data: [DONE]\n\n"
    mock_registry.save_session_snapshot.assert_called_once_with("test_token_XYZ", provider, mock_manager)


@pytest.mark.asyncio
async def test_chat_completions_stateful_streaming_interrupt_does_not_emit_artifact_chunk(mocker, provider):
    """Verify interrupting WebAPI streaming does not emit an artifact chunk."""
    from app.schemas.request import OpenAIChatRequest
    from app.services.providers.gemini.session_manager import SessionManager, SessionRegistry

    mock_client = mocker.Mock()
    mock_client.client.account_status.name = "AVAILABLE"
    mock_registry = mocker.Mock(spec=SessionRegistry)
    mock_manager = mocker.Mock(spec=SessionManager)

    async def mock_generator(*args, **kwargs):
        yield {
            "type": "chunk",
            "text_delta": "Stateful delta content",
            "is_reused": True,
        }
        yield {
            "type": "interrupt",
            "interrupted": True,
            "reason": "timeout",
            "is_reused": True,
        }

    mock_manager.get_streaming_response_stateful = mock_generator
    mock_registry.get_session = mocker.AsyncMock(return_value=mock_manager)
    mock_registry.save_session_snapshot = mocker.AsyncMock()

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=mock_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)

    request = OpenAIChatRequest(
        messages=[{"role": "user", "content": "I am Ali. What is my name?"}],
        model="gemini-3-flash",
        stream=True,
        conversation_id="test_token_XYZ",
    )

    response = await provider.chat_completions(request)
    assert response is not None

    from fastapi.responses import StreamingResponse
    assert isinstance(response, StreamingResponse)

    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)

    assert len(chunks) == 2
    assert json.loads(chunks[0][6:-2])["choices"][0]["delta"]["content"] == "Stateful delta content"
    assert chunks[1] == "data: [DONE]\n\n"


def test_transform_messages_formatting():
    """Verify transform_messages formatting behavior for different roles and tools."""
    from app.services.providers.gemini.session_manager import transform_messages
    
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
        {"role": "assistant", "tool_calls": [{"function": {"name": "get_weather", "arguments": '{"loc": "SF"}'}}]},
        {"role": "tool", "tool_call_id": "123", "content": "Sunny"}
    ]
    
    result = transform_messages(messages, tools_prompt="Tools list")
    
    # Verify role line conversion
    assert "System: You are a helpful assistant.\n\nTools list" in result[0]
    assert "User: Hello" in result
    assert "Assistant: Hi there!" in result
    assert "Assistant called tool get_weather: {\"loc\": \"SF\"}" in result
    assert "Tool result [123]: Sunny" in result


def test_default_metadata_leak_security_regression():
    """Verify that multiple ChatSession creations do not leak metadata and DEFAULT_METADATA remains pristine."""
    from gemini_webapi.constants import DEFAULT_METADATA
    from app.services.providers.gemini.webapi_client import MyGeminiClient
    
    client = MyGeminiClient(secure_1psid="test", secure_1psidts="test")
    
    session_a = client.start_chat(model="gemini-3-flash")
    session_b = client.start_chat(model="gemini-3-flash")
    
    # Verify initial empty state
    assert session_a.cid == ""
    assert session_b.cid == ""
    
    # Update metadata on session A exactly as Google response parsing does
    mock_google_metadata = ["thread_A_123", "reply_A_456", "rcid_A_789", None, None, None, None, None, None, "context_A"]
    session_a.metadata = mock_google_metadata
    
    # Assert session A has the correct values
    assert session_a.cid == "thread_A_123"
    
    # CRITICAL SECURITY CHECKS:
    # 1. Assert session B was NOT contaminated and is still empty
    assert session_b.cid == ""
    
    # 2. Assert global DEFAULT_METADATA list remains pristine
    assert DEFAULT_METADATA[0] == ""
    assert DEFAULT_METADATA[1] == ""


@pytest.mark.asyncio
async def test_my_gemini_client_forwards_temporary_flag(mocker):
    """Verify the Gemini WebAPI wrapper forwards temporary=True to direct client calls."""
    from app.services.providers.gemini.webapi_client import MyGeminiClient

    client = MyGeminiClient(secure_1psid="test", secure_1psidts="test")
    underlying_client = mocker.Mock()
    underlying_client.generate_content = mocker.AsyncMock(
        return_value=SimpleNamespace(text="ok")
    )

    async def mock_stream():
        yield SimpleNamespace(text_delta="chunk")

    underlying_client.generate_content_stream = mocker.Mock(return_value=mock_stream())
    client.client = underlying_client

    await client.generate_content("hello", "gemini-3-flash", temporary=True)
    stream = await client.generate_content_stream("hello", "gemini-3-flash", temporary=True)

    assert hasattr(stream, "__aiter__")
    underlying_client.generate_content.assert_awaited_once_with(
        "hello",
        model="gemini-3-flash",
        files=None,
        gem=None,
        temporary=True,
    )
    underlying_client.generate_content_stream.assert_called_once_with(
        "hello",
        model="gemini-3-flash",
        files=None,
        gem=None,
        temporary=True,
    )
