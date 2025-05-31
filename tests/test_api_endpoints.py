"""
Unit tests for API endpoint handlers in webaitoapi.main.
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

# Import the FastAPI app instance from your main application
# We need to be careful with how global clients are initialized.
# For testing, we'll often patch them where they are USED.
from webaitoapi.main import app, ENABLED_AI, ClaudeModels, GeminiModels, DeepseekModels, OpenAIChatRequest, Message

# Using pytest-asyncio for async test functions
pytestmark = pytest.mark.asyncio

@pytest.fixture
def client():
    """Test client for the FastAPI app."""
    with TestClient(app) as c:
        yield c

# --- Tests for /gemini endpoint ---
async def test_gemini_chat_endpoint_success(client):
    """Test successful response from /gemini endpoint."""
    # Ensure Gemini is marked as enabled for this test
    original_gemini_enabled = ENABLED_AI.get("gemini")
    ENABLED_AI["gemini"] = True

    # Mock the gemini_client and its methods
    # The client is global in main.py, so we patch it where it's defined or used.
    # Let's assume it's used as `main.gemini_client` within the endpoint.
    # If it's imported directly, the patch path would be different.
    # Based on main.py structure, it's a global `gemini_client`.
    mock_gemini_response = AsyncMock()
    mock_gemini_response.text = "Gemini says hello!"

    # Patching the global variable 'gemini_client' in the 'webaitoapi.main' module
    with patch("webaitoapi.main.gemini_client", new_callable=AsyncMock) as mock_client_instance:
        mock_client_instance.generate_content = AsyncMock(return_value=mock_gemini_response)

        response = client.post(
            "/gemini",
            json={"message": "Hello Gemini", "model": GeminiModels.PRO.value}
        )

    assert response.status_code == 200
    assert response.json() == {"response": "Gemini says hello!", "model_used": GeminiModels.PRO.value}

    # Restore original ENABLED_AI state
    if original_gemini_enabled is None:
        ENABLED_AI.pop("gemini", None)
    else:
        ENABLED_AI["gemini"] = original_gemini_enabled


async def test_gemini_chat_endpoint_disabled(client):
    """Test /gemini endpoint when Gemini client is disabled."""
    original_gemini_enabled = ENABLED_AI.get("gemini")
    ENABLED_AI["gemini"] = False # Explicitly disable for this test

    response = client.post(
        "/gemini",
        json={"message": "Hello Gemini", "model": GeminiModels.PRO.value}
    )

    assert response.status_code == 400 # Or whatever status code is set for disabled
    assert "Gemini client is disabled" in response.json()["detail"]

    if original_gemini_enabled is None:
        ENABLED_AI.pop("gemini", None)
    else:
        ENABLED_AI["gemini"] = original_gemini_enabled

async def test_gemini_chat_endpoint_client_not_initialized(client):
    """Test /gemini endpoint when Gemini client is enabled but not initialized."""
    original_gemini_enabled = ENABLED_AI.get("gemini")
    ENABLED_AI["gemini"] = True

    # Patch the global gemini_client to be None (simulating not initialized)
    with patch("webaitoapi.main.gemini_client", None):
        response = client.post(
            "/gemini",
            json={"message": "Hello Gemini", "model": GeminiModels.PRO.value}
        )

    assert response.status_code == 400 # As per current main.py logic for not initialized client
    assert "Gemini client is disabled or not initialized" in response.json()["detail"] # Message from main.py

    if original_gemini_enabled is None:
        ENABLED_AI.pop("gemini", None)
    else:
        ENABLED_AI["gemini"] = original_gemini_enabled


# --- Tests for /claude endpoint ---
async def test_claude_chat_endpoint_success_non_streaming(client):
    """Test successful non-streaming response from /claude endpoint."""
    original_claude_enabled = ENABLED_AI.get("claude")
    ENABLED_AI["claude"] = True

    mock_claude_response = "Claude says hello non-streaming!"

    with patch("webaitoapi.main.claude_client", new_callable=AsyncMock) as mock_client_instance:
        mock_client_instance.send_message = AsyncMock(return_value=mock_claude_response)

        response = client.post(
            "/claude",
            json={"message": "Hello Claude", "model": ClaudeModels.SONNET_5.value, "stream": False}
        )

    assert response.status_code == 200
    assert response.json() == {"response": mock_claude_response, "model_used": ClaudeModels.SONNET_5.value}

    if original_claude_enabled is None:
        ENABLED_AI.pop("claude", None)
    else:
        ENABLED_AI["claude"] = original_claude_enabled

# TODO: Add test for Claude streaming if possible with TestClient and AsyncMock for async generator
# Testing streaming endpoints with TestClient requires careful handling of the response content.

async def test_claude_chat_endpoint_disabled(client):
    """Test /claude endpoint when Claude client is disabled."""
    original_claude_enabled = ENABLED_AI.get("claude")
    ENABLED_AI["claude"] = False

    response = client.post(
        "/claude",
        json={"message": "Hello Claude", "model": ClaudeModels.SONNET_5.value}
    )

    assert response.status_code == 400
    assert "Claude client is disabled or not initialized" in response.json()["detail"]

    if original_claude_enabled is None:
        ENABLED_AI.pop("claude", None)
    else:
        ENABLED_AI["claude"] = original_claude_enabled

# --- Tests for /deepseek endpoint ---
async def test_deepseek_chat_endpoint_success_non_streaming(client):
    """Test successful non-streaming response from /deepseek endpoint."""
    original_deepseek_enabled = ENABLED_AI.get("deepseek")
    ENABLED_AI["deepseek"] = True

    # Mock the chat method to be an async generator yielding a single chunk
    async def mock_chat_generator(*args, **kwargs):
        yield "Deepseek says hello non-streaming!"

    with patch("webaitoapi.main.deepseek_client", new_callable=AsyncMock) as mock_client_instance:
        # If stream=False, the endpoint iterates over an async generator
        mock_client_instance.chat = AsyncMock(side_effect=mock_chat_generator)

        response = client.post(
            "/deepseek",
            json={"message": "Hello Deepseek", "model": DeepseekModels.CHAT.value, "stream": False}
        )

    assert response.status_code == 200
    # The endpoint aggregates the stream for non-streaming requests
    assert response.json() == {"response": "Deepseek says hello non-streaming!", "model_used": DeepseekModels.CHAT.value}

    if original_deepseek_enabled is None:
        ENABLED_AI.pop("deepseek", None)
    else:
        ENABLED_AI["deepseek"] = original_deepseek_enabled

# TODO: Add test for Deepseek streaming

async def test_deepseek_chat_endpoint_disabled(client):
    """Test /deepseek endpoint when Deepseek client is disabled."""
    original_deepseek_enabled = ENABLED_AI.get("deepseek")
    ENABLED_AI["deepseek"] = False

    response = client.post(
        "/deepseek",
        json={"message": "Hello Deepseek", "model": DeepseekModels.CHAT.value}
    )

    assert response.status_code == 400
    assert "Deepseek client is disabled or not initialized" in response.json()["detail"]

    if original_deepseek_enabled is None:
        ENABLED_AI.pop("deepseek", None)
    else:
        ENABLED_AI["deepseek"] = original_deepseek_enabled


# --- Tests for /v1/chat/completions endpoint ---
async def test_openai_chat_completions_gemini_non_streaming(client):
    """Test /v1/chat/completions with Gemini, non-streaming."""
    original_gemini_enabled = ENABLED_AI.get("gemini")
    ENABLED_AI["gemini"] = True

    mock_gemini_response_text = "OpenAI-formatted Gemini response"
    mock_gemini_api_response = AsyncMock()
    mock_gemini_api_response.text = mock_gemini_response_text

    with patch("webaitoapi.main.gemini_client", new_callable=AsyncMock) as mock_client_instance:
        mock_client_instance.generate_content = AsyncMock(return_value=mock_gemini_api_response)

        payload = {
            "messages": [{"role": "user", "content": "Hello OpenAI Gemini"}],
            "model": GeminiModels.PRO.value, # Explicitly use Gemini model enum value
            "stream": False
        }
        response = client.post("/v1/chat/completions", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["model"] == GeminiModels.PRO.value
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["content"] == mock_gemini_response_text
    assert data["choices"][0]["finish_reason"] == "stop"
    assert data["usage"]["prompt_tokens"] == 0 # Placeholder

    if original_gemini_enabled is None:
        ENABLED_AI.pop("gemini", None)
    else:
        ENABLED_AI["gemini"] = original_gemini_enabled

async def test_openai_chat_completions_claude_non_streaming(client):
    """Test /v1/chat/completions with Claude, non-streaming."""
    original_claude_enabled = ENABLED_AI.get("claude")
    ENABLED_AI["claude"] = True

    mock_claude_response_text = "OpenAI-formatted Claude response"

    with patch("webaitoapi.main.claude_client", new_callable=AsyncMock) as mock_client_instance:
        mock_client_instance.send_message = AsyncMock(return_value=mock_claude_response_text)

        payload = {
            "messages": [{"role": "user", "content": "Hello OpenAI Claude"}],
            "model": ClaudeModels.SONNET_5.value,
            "stream": False
        }
        response = client.post("/v1/chat/completions", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["model"] == ClaudeModels.SONNET_5.value
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["content"] == mock_claude_response_text
    assert data["choices"][0]["finish_reason"] == "stop"

    if original_claude_enabled is None:
        ENABLED_AI.pop("claude", None)
    else:
        ENABLED_AI["claude"] = original_claude_enabled


async def test_openai_chat_completions_default_ai_non_streaming(client):
    """Test /v1/chat/completions with default AI (Gemini), non-streaming."""
    original_gemini_enabled = ENABLED_AI.get("gemini")
    ENABLED_AI["gemini"] = True # Assume Gemini is default and enabled

    # Mock config to ensure Gemini is default
    with patch('webaitoapi.main.config') as mock_config:
        mock_config.__getitem__.side_effect = lambda key: {
            "AI": {"default_ai": "gemini", "default_model_gemini": GeminiModels.PRO.value}
        }[key]

        mock_gemini_response_text = "Default AI response"
        mock_gemini_api_response = AsyncMock()
        mock_gemini_api_response.text = mock_gemini_response_text

        with patch("webaitoapi.main.gemini_client", new_callable=AsyncMock) as mock_client_instance:
            mock_client_instance.generate_content = AsyncMock(return_value=mock_gemini_api_response)

            payload = {
                "messages": [{"role": "user", "content": "Hello Default AI"}],
                # No model specified, should use default from config
                "stream": False
            }
            response = client.post("/v1/chat/completions", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["model"] == GeminiModels.PRO.value # Default Gemini model
    assert data["choices"][0]["message"]["content"] == mock_gemini_response_text

    if original_gemini_enabled is None:
        ENABLED_AI.pop("gemini", None)
    else:
        ENABLED_AI["gemini"] = original_gemini_enabled

async def test_openai_chat_completions_no_user_message(client):
    """Test /v1/chat/completions when no user message is provided."""
    payload = {
        "messages": [{"role": "assistant", "content": "I have no questions"}],
        "model": GeminiModels.PRO.value
    }
    response = client.post("/v1/chat/completions", json=payload)
    assert response.status_code == 400
    assert "No user message found" in response.json()["detail"]

# TODO: Add tests for streaming with /v1/chat/completions for Claude and Deepseek.
# This requires more complex mocking of async generators and checking streamed SSE content.
# Example for Claude streaming:
# async def test_openai_chat_completions_claude_streaming(client):
#     ENABLED_AI["claude"] = True
#     async def mock_claude_stream(*args, **kwargs):
#         yield json.dumps({"id": "1", "object": "chat.completion.chunk", ..., "choices": [{"delta": {"content": "Hello "}}]})
#         yield json.dumps({"id": "1", "object": "chat.completion.chunk", ..., "choices": [{"delta": {"content": "Claude"}}]})
#
#     with patch("webaitoapi.main.claude_client", new_callable=AsyncMock) as mock_client:
#         mock_client.stream_message = mock_claude_stream # No, this needs to be an AsyncMock that returns an async generator
#
#         payload = {
#             "messages": [{"role": "user", "content": "Hello OpenAI Claude Stream"}],
#             "model": ClaudeModels.SONNET_5.value,
#             "stream": True
#         }
#         response = client.post("/v1/chat/completions", json=payload)
#     assert response.status_code == 200
#     # Need to iterate through response.text or response.iter_lines() for SSE
#     # and parse each "data: ..." line.
#     ENABLED_AI["claude"] = False # cleanup
