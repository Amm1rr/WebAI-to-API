import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

async def _get_openapi_schema():
    """Helper to fetch and return the OpenAPI schema."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/openapi.json")
    assert response.status_code == 200
    return response.json()

@pytest.mark.asyncio
async def test_openapi_legacy_endpoint_metadata():
    """Verify metadata for legacy endpoints."""
    schema = await _get_openapi_schema()
    
    # Check /gemini
    gemini_path = schema["paths"].get("/gemini")
    assert gemini_path is not None
    assert gemini_path["post"].get("deprecated") is True
    assert "Legacy" in gemini_path["post"]["tags"]
    assert "Legacy" in gemini_path["post"]["summary"]
    assert "OpenAI-compatible" in gemini_path["post"]["description"]

    # Check /gemini-chat
    gemini_chat_path = schema["paths"].get("/gemini-chat")
    assert gemini_chat_path is not None
    assert gemini_chat_path["post"].get("deprecated") is True
    assert "Legacy" in gemini_chat_path["post"]["tags"]
    assert "Legacy" in gemini_chat_path["post"]["summary"]
    assert "does not survive server restarts" in gemini_chat_path["post"]["description"]

@pytest.mark.asyncio
async def test_openapi_translate_endpoint_metadata():
    """Verify metadata for the translate endpoint."""
    schema = await _get_openapi_schema()
    
    translate_path = schema["paths"].get("/translate")
    assert translate_path is not None
    assert translate_path["post"].get("deprecated") is not True
    assert "Translation" in translate_path["post"]["tags"]
    assert "Translate Extension Compatibility" in translate_path["post"]["summary"]
    assert "shared global in-memory session" in translate_path["post"]["description"]
    assert "temporary requests" in translate_path["post"]["description"]
    assert "/v1/chat/completions" in translate_path["post"]["description"]

@pytest.mark.asyncio
async def test_openapi_chat_endpoint_metadata():
    """Verify metadata for primary chat endpoints."""
    schema = await _get_openapi_schema()
    
    # Check /v1/chat/completions
    chat_path = schema["paths"].get("/v1/chat/completions")
    assert chat_path is not None
    assert "Chat" in chat_path["post"]["tags"]
    assert "OpenAI-Compatible Chat Completions" in chat_path["post"]["summary"]
    assert "recommended API" in chat_path["post"]["description"]
    assert "Gemini WebAPI supports file content parts" in chat_path["post"]["description"]
    assert "request-scoped" in chat_path["post"]["description"]
    assert "interleaving" in chat_path["post"]["description"]
    assert "docs/api.md" in chat_path["post"]["description"]

    # Check /v1/models
    models_path = schema["paths"].get("/v1/models")
    assert models_path is not None
    assert "Chat" in models_path["get"]["tags"]
    assert "List Available Models" in models_path["get"]["summary"]


@pytest.mark.asyncio
async def test_openapi_chat_endpoint_documents_success_responses():
    schema = await _get_openapi_schema()

    chat_post = schema["paths"]["/v1/chat/completions"]["post"]
    responses = chat_post["responses"]

    assert "200" in responses
    assert "422" in responses

    success = responses["200"]
    assert success["description"] == "Successful Response"
    content = success["content"]
    assert "application/json" in content
    assert "text/event-stream" in content

    json_schema = content["application/json"]["schema"]
    assert json_schema != {}
    assert "choices" in json_schema["properties"]
    choice_schema = json_schema["properties"]["choices"]["items"]
    assert "artifacts" in choice_schema["properties"]
    assert "message" in choice_schema["properties"]
    assert "thoughts" not in json.dumps(json_schema)

    json_examples = content["application/json"]["examples"]
    assert "bufferedTextOnly" in json_examples
    assert "bufferedArtifacts" in json_examples
    assert json_examples["bufferedTextOnly"]["value"]["choices"][0]["message"]["content"] == "Hello!"
    assert "artifacts" in json_examples["bufferedArtifacts"]["value"]["choices"][0]

    stream_content = content["text/event-stream"]
    assert stream_content["schema"]["type"] == "string"
    stream_examples = stream_content["examples"]
    assert "streamTextDelta" in stream_examples
    assert "streamFinalArtifacts" in stream_examples
    assert "streamDone" in stream_examples
    assert 'delta":{"role":"assistant","content":"Hello"}' in stream_examples["streamTextDelta"]["value"]
    assert '"delta":{}' in stream_examples["streamFinalArtifacts"]["value"]
    assert '"finish_reason":"stop"' in stream_examples["streamFinalArtifacts"]["value"]
    assert '"artifacts":[{' in stream_examples["streamFinalArtifacts"]["value"]
    assert "data: [DONE]" in stream_examples["streamDone"]["value"]
    assert "thoughts" not in json.dumps(stream_examples)

@pytest.mark.asyncio
async def test_openapi_authentication_endpoint_metadata():
    """Verify metadata for authentication endpoints."""
    schema = await _get_openapi_schema()
    
    # Check /v1/auth/status
    status_path = schema["paths"].get("/v1/auth/status")
    assert status_path is not None
    assert "Authentication" in status_path["get"]["tags"]
    assert "Get Authentication Status" in status_path["get"]["summary"]

    # Check /v1/auth/login
    login_path = schema["paths"].get("/v1/auth/login")
    assert login_path is not None
    assert "Authentication" in login_path["post"]["tags"]
    assert "Trigger Authentication Login" in login_path["post"]["summary"]

@pytest.mark.asyncio
async def test_openapi_compatibility_endpoint_metadata():
    """Verify metadata for compatibility endpoints."""
    schema = await _get_openapi_schema()
    
    v1beta_path = schema["paths"].get("/v1beta/models/{model_path}")
    assert v1beta_path is not None
    assert "Compatibility" in v1beta_path["post"]["tags"]
    assert "Google Generative AI Compatibility Endpoint" in v1beta_path["post"]["summary"]
    assert "not guaranteed to provide full protocol parity" in v1beta_path["post"]["description"]


@pytest.mark.asyncio
async def test_openapi_chat_request_supports_content_parts():
    """Verify the primary chat request schema documents string and multimodal content."""
    schema = await _get_openapi_schema()

    request_schema = schema["components"]["schemas"]["OpenAIChatRequest"]
    message_schema = schema["components"]["schemas"]["OpenAIChatMessage"]
    text_part_schema = schema["components"]["schemas"]["OpenAIChatTextContentPart"]
    file_part_schema = schema["components"]["schemas"]["OpenAIChatFileContentPart"]
    file_payload_schema = schema["components"]["schemas"]["OpenAIChatFilePayload"]
    chat_path = schema["paths"]["/v1/chat/completions"]["post"]
    request_examples = chat_path["requestBody"]["content"]["application/json"]["examples"]

    content_schema = message_schema["properties"]["content"]
    assert "anyOf" in content_schema
    assert any(item.get("type") == "string" for item in content_schema["anyOf"])
    assert "Either a plain string or an array of content parts." in content_schema["description"]

    messages_schema = request_schema["properties"]["messages"]
    assert messages_schema["items"]["$ref"].endswith("/OpenAIChatMessage")
    assert "OpenAI-compatible chat request" in request_schema["description"]

    assert message_schema["description"].startswith("OpenAI-compatible chat message")
    assert text_part_schema["properties"]["type"]["const"] == "text"
    assert text_part_schema["description"] == "OpenAI-style text content part."
    assert text_part_schema["properties"]["text"]["description"] == "Plain text for this content part."
    assert file_part_schema["properties"]["type"]["const"] == "file"
    assert file_part_schema["description"].startswith("OpenAI-style file attachment content part.")
    assert file_part_schema["properties"]["file"]["description"] == "File attachment metadata and base64 data URL payload."
    assert file_payload_schema["description"].startswith("File attachment payload for Gemini WebAPI file parts.")
    assert file_payload_schema["properties"]["filename"]["type"] == "string"
    assert "Original filename used for validation" in file_payload_schema["properties"]["filename"]["description"]
    assert file_payload_schema["properties"]["file_data"]["type"] == "string"
    assert "Base64 data URL" in file_payload_schema["properties"]["file_data"]["description"]
    assert "Remote URLs, filesystem paths, and file_id are not supported." in file_payload_schema["properties"]["file_data"]["description"]

    assert "textOnly" in request_examples
    assert "fileRequest" in request_examples
    assert request_examples["textOnly"]["value"]["messages"][0]["content"] == "Hello!"
    file_example = request_examples["fileRequest"]["value"]["messages"][0]["content"][1]
    assert file_example["type"] == "file"
    assert file_example["file"]["filename"] == "invoice.pdf"
    assert file_example["file"]["file_data"] == "data:application/pdf;base64,JVBERi0xLjQK"
    assert len(file_example["file"]["file_data"]) < 64

@pytest.mark.asyncio
async def test_openapi_utility_endpoint_metadata():
    """Verify metadata for utility endpoints."""
    schema = await _get_openapi_schema()
    
    gems_path = schema["paths"].get("/v1/gems")
    assert gems_path is not None
    assert "Utilities" in gems_path["get"]["tags"]
    assert "List Available Gems" in gems_path["get"]["summary"]
