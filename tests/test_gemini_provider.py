import pytest
import json
from types import SimpleNamespace
from fastapi import HTTPException
from app.services.providers.gemini import GeminiProvider

@pytest.fixture
def provider():
    return GeminiProvider()

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


@pytest.mark.asyncio
async def test_chat_completions_stateful_buffered(mocker, provider):
    """Verify chat_completions retrieves SessionManager and executes stateful buffered response."""
    from app.schemas.request import OpenAIChatRequest
    from app.services.session_manager import SessionManager, SessionRegistry
    
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
    mocker.patch("app.services.providers.gemini.get_gemini_client", return_value=mock_client)
    mocker.patch("app.services.providers.gemini.get_gemini_chat_registry", return_value=mock_registry)
    
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
    from app.services.session_manager import SessionRegistry

    mocker.patch("app.services.providers.gemini.get_gemini_client", return_value=mocker.Mock())
    mock_registry = mocker.Mock(spec=SessionRegistry)
    mock_registry.get_session = mocker.AsyncMock()
    mocker.patch("app.services.providers.gemini.get_gemini_chat_registry", return_value=mock_registry)

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
    from app.services.session_manager import SessionRegistry

    mocker.patch("app.services.providers.gemini.get_gemini_client", return_value=mocker.Mock())
    mock_registry = mocker.Mock(spec=SessionRegistry)
    mock_registry.get_session = mocker.AsyncMock()
    mocker.patch("app.services.providers.gemini.get_gemini_chat_registry", return_value=mock_registry)

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
    from app.services.session_manager import SessionRegistry

    mock_client = mocker.Mock()
    mock_client.client.account_status.name = "UNAUTHENTICATED"
    mocker.patch("app.services.providers.gemini.get_gemini_client", return_value=mock_client)

    mock_registry = mocker.Mock(spec=SessionRegistry)
    mock_registry.get_session = mocker.AsyncMock()
    mocker.patch("app.services.providers.gemini.get_gemini_chat_registry", return_value=mock_registry)

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
    from app.services.session_manager import SessionRegistry

    mock_client = SimpleNamespace(
        client=SimpleNamespace(account_status=SimpleNamespace())
    )
    mocker.patch("app.services.providers.gemini.get_gemini_client", return_value=mock_client)

    mock_registry = mocker.Mock(spec=SessionRegistry)
    mock_registry.get_session = mocker.AsyncMock()
    mocker.patch("app.services.providers.gemini.get_gemini_chat_registry", return_value=mock_registry)

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
async def test_chat_completions_stateful_streaming(mocker, provider):
    """Verify chat_completions retrieves SessionManager and executes stateful streaming response with SSE format."""
    from app.schemas.request import OpenAIChatRequest
    from app.services.session_manager import SessionManager, SessionRegistry
    
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

    mock_manager.get_streaming_response_stateful = mock_generator
    mock_registry.get_session = mocker.AsyncMock(return_value=mock_manager)
    mock_registry.save_session_snapshot = mocker.AsyncMock()
    
    mocker.patch("app.services.providers.gemini.get_gemini_client", return_value=mock_client)
    mocker.patch("app.services.providers.gemini.get_gemini_chat_registry", return_value=mock_registry)
    
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


def test_transform_messages_formatting():
    """Verify transform_messages formatting behavior for different roles and tools."""
    from app.services.session_manager import transform_messages
    
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
    from models.gemini import MyGeminiClient
    
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
