import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.request import OpenAIChatRequest
from app.services.providers.atlas.provider import AtlasProvider
from app.services.multimodal import (
    cleanup_staged_files,
    normalize_openai_chat_messages,
)
from app.services.providers.gemini.provider import GeminiProvider
from app.services.providers.gemini.webapi_adapter import GeminiWebAPIAdapter


VALID_DATA_URL = "data:application/pdf;base64,JVBERi0xLjQK"
JSON_DATA_URL = "data:application/json;base64,eyJrZXkiOiAidmFsdWUifQ=="
XML_DATA_URL = "data:application/xml;base64,PHJvb3Q+dmFsdWU8L3Jvb3Q+"
XLSX_DATA_URL = "data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,UEsDBA=="


def _file_part(
    filename: str = "invoice.pdf",
    data_url: str = VALID_DATA_URL,
):
    return {
        "type": "file",
        "file": {
            "filename": filename,
            "file_data": data_url,
        },
    }


def _text_part(text: str = "Summarize this file."):
    return {"type": "text", "text": text}


def test_openai_chat_request_accepts_string_content():
    request = OpenAIChatRequest(
        messages=[{"role": "user", "content": "Hello"}],
        model="gemini-3-flash",
    )

    assert request.messages[0].content == "Hello"


def test_openai_chat_request_accepts_text_and_file_parts():
    request = OpenAIChatRequest(
        messages=[
            {
                "role": "user",
                "content": [
                    _text_part(),
                    _file_part(),
                ],
            }
        ],
        model="gemini-3-flash",
    )

    assert isinstance(request.messages[0].content, list)
    assert request.messages[0].content[0].type == "text"
    assert request.messages[0].content[1].type == "file"


@pytest.mark.parametrize(
    "filename,data_url,expected_mime",
    [
        ("config.json", JSON_DATA_URL, "application/json"),
        ("layout.xml", XML_DATA_URL, "application/xml"),
        ("sheet.xlsx", XLSX_DATA_URL, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ],
)
def test_normalize_openai_chat_messages_accepts_new_verified_formats(
    filename,
    data_url,
    expected_mime,
):
    request = OpenAIChatRequest(
        messages=[
            {
                "role": "user",
                "content": [
                    _text_part("Read this file."),
                    _file_part(filename=filename, data_url=data_url),
                ],
            }
        ],
        model="gemini-3-flash",
    )

    normalized = normalize_openai_chat_messages(
        request.messages,
        allow_file_parts=True,
    )

    assert normalized.messages[0]["content"] == "Read this file."
    assert len(normalized.files) == 1
    assert normalized.files[0].exists()
    assert normalized.files[0].name.endswith(filename)
    assert expected_mime in data_url


def test_normalize_openai_chat_messages_stages_file_and_keeps_text():
    request = OpenAIChatRequest(
        messages=[
            {
                "role": "user",
                "content": [
                    _text_part("Summarize this document."),
                    _file_part(),
                ],
            }
        ],
        model="gemini-3-flash",
    )

    normalized = normalize_openai_chat_messages(
        request.messages,
        allow_file_parts=True,
    )

    assert normalized.messages[0]["content"] == "Summarize this document."
    assert len(normalized.files) == 1
    assert normalized.files[0].exists()
    assert normalized.cleanup_dir is not None


def test_normalize_openai_chat_messages_accepts_text_only_parts():
    request = OpenAIChatRequest(
        messages=[
            {
                "role": "user",
                "content": [
                    _text_part("Hello"),
                    _text_part("world"),
                ],
            }
        ],
        model="gemini-3-flash",
    )

    normalized = normalize_openai_chat_messages(
        request.messages,
        allow_file_parts=True,
    )

    assert normalized.messages[0]["content"] == "Hello\n\nworld"
    assert normalized.files == []


def test_normalize_openai_chat_messages_rejects_malformed_data_url():
    request = OpenAIChatRequest(
        messages=[
            {
                "role": "user",
                "content": [_file_part(data_url="not-a-data-url")],
            }
        ],
        model="gemini-3-flash",
    )

    with pytest.raises(HTTPException) as exc_info:
        normalize_openai_chat_messages(
            request.messages,
            allow_file_parts=True,
        )

    assert exc_info.value.status_code == 400


def test_normalize_openai_chat_messages_rejects_remote_url():
    request = OpenAIChatRequest(
        messages=[
            {
                "role": "user",
                "content": [
                    _file_part(data_url="https://example.com/invoice.pdf"),
                ],
            }
        ],
        model="gemini-3-flash",
    )

    with pytest.raises(HTTPException) as exc_info:
        normalize_openai_chat_messages(
            request.messages,
            allow_file_parts=True,
        )

    assert exc_info.value.status_code == 400


def test_normalize_openai_chat_messages_rejects_raw_path():
    request = OpenAIChatRequest(
        messages=[
            {
                "role": "user",
                "content": [
                    _file_part(data_url="/tmp/invoice.pdf"),
                ],
            }
        ],
        model="gemini-3-flash",
    )

    with pytest.raises(HTTPException) as exc_info:
        normalize_openai_chat_messages(
            request.messages,
            allow_file_parts=True,
        )

    assert exc_info.value.status_code == 400


def test_normalize_openai_chat_messages_rejects_unsupported_part_type():
    with pytest.raises(ValidationError):
        OpenAIChatRequest(
            messages=[
                {
                    "role": "user",
                    "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA="}}],
                }
            ],
            model="gemini-3-flash",
        )


def test_normalize_openai_chat_messages_rejects_xls_format():
    request = OpenAIChatRequest(
        messages=[
            {
                "role": "user",
                "content": [
                    _file_part(
                        filename="legacy.xls",
                        data_url="data:application/vnd.ms-excel;base64,UEsDBA==",
                    ),
                ],
            }
        ],
        model="gemini-3-flash",
    )

    with pytest.raises(HTTPException) as exc_info:
        normalize_openai_chat_messages(
            request.messages,
            allow_file_parts=True,
        )

    assert exc_info.value.status_code == 400


def test_normalize_openai_chat_messages_rejects_file_parts_in_assistant_role():
    request = OpenAIChatRequest(
        messages=[
            {
                "role": "assistant",
                "content": [_file_part()],
            }
        ],
        model="gemini-3-flash",
    )

    with pytest.raises(HTTPException) as exc_info:
        normalize_openai_chat_messages(
            request.messages,
            allow_file_parts=True,
        )

    assert exc_info.value.status_code == 400


def test_normalize_openai_chat_messages_rejects_oversized_single_file(mocker):
    mocker.patch("app.services.multimodal.MAX_FILE_SIZE_BYTES", 1)

    request = OpenAIChatRequest(
        messages=[
            {
                "role": "user",
                "content": [
                    _file_part(data_url="data:application/pdf;base64,YWI="),
                ],
            }
        ],
        model="gemini-3-flash",
    )

    with pytest.raises(HTTPException) as exc_info:
        normalize_openai_chat_messages(
            request.messages,
            allow_file_parts=True,
        )

    assert exc_info.value.status_code == 413


def test_normalize_openai_chat_messages_rejects_too_many_files(mocker):
    mocker.patch("app.services.multimodal.MAX_FILE_COUNT", 1)

    request = OpenAIChatRequest(
        messages=[
            {
                "role": "user",
                "content": [
                    _file_part(filename="one.pdf"),
                    _file_part(filename="two.pdf"),
                ],
            }
        ],
        model="gemini-3-flash",
    )

    with pytest.raises(HTTPException) as exc_info:
        normalize_openai_chat_messages(
            request.messages,
            allow_file_parts=True,
        )

    assert exc_info.value.status_code == 413


def test_normalize_openai_chat_messages_rejects_total_file_size_limit(mocker):
    mocker.patch("app.services.multimodal.MAX_TOTAL_FILE_SIZE_BYTES", 1)

    request = OpenAIChatRequest(
        messages=[
            {
                "role": "user",
                "content": [
                    _file_part(filename="one.pdf", data_url="data:application/pdf;base64,YQ=="),
                    _file_part(filename="two.pdf", data_url="data:application/pdf;base64,YQ=="),
                ],
            }
        ],
        model="gemini-3-flash",
    )

    with pytest.raises(HTTPException) as exc_info:
        normalize_openai_chat_messages(
            request.messages,
            allow_file_parts=True,
        )

    assert exc_info.value.status_code == 413


@pytest.mark.asyncio
async def test_cleanup_staged_files_removes_temp_dir():
    request = OpenAIChatRequest(
        messages=[
            {
                "role": "user",
                "content": [_text_part(), _file_part()],
            }
        ],
        model="gemini-3-flash",
    )

    normalized = normalize_openai_chat_messages(
        request.messages,
        allow_file_parts=True,
    )

    cleanup_dir = normalized.cleanup_dir
    assert cleanup_dir is not None
    assert cleanup_dir.exists()

    await cleanup_staged_files(normalized)

    assert not cleanup_dir.exists()


@pytest.mark.asyncio
async def test_gemini_webapi_adapter_passes_files_to_buffered_session(mocker):
    from pathlib import Path
    from types import SimpleNamespace

    provider = GeminiProvider()
    adapter = GeminiWebAPIAdapter(provider)

    mock_client = SimpleNamespace(client=SimpleNamespace(account_status=SimpleNamespace(name="AVAILABLE")))
    mock_registry = mocker.Mock()
    mock_manager = mocker.Mock()
    mock_response = SimpleNamespace(text="buffered response")
    mock_manager.get_response_stateful = mocker.AsyncMock(return_value=(mock_response, False))
    mock_registry.get_session = mocker.AsyncMock(return_value=mock_manager)
    mock_registry.save_session_snapshot = mocker.AsyncMock()

    normalized = SimpleNamespace(
        messages=[{"role": "user", "content": "Summarize this document."}],
        files=[Path("/tmp/staged/invoice.pdf")],
        cleanup_dir=Path("/tmp/staged"),
    )

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=mock_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)
    cleanup = mocker.patch(
        "app.services.providers.gemini.webapi_adapter.cleanup_staged_files",
        mocker.AsyncMock(),
    )

    request = OpenAIChatRequest(
        messages=[
            {
                "role": "user",
                "content": "Summarize this document.",
            }
        ],
        model="gemini-3-flash",
    )
    object.__setattr__(request, "_normalized_openai_chat_messages", normalized)

    result = await adapter.chat_completions(request, "conv-123", True, "")

    assert result["conversation_id"] == "conv-123"
    assert result["reused_conversation"] is False
    mock_manager.get_response_stateful.assert_called_once()
    assert mock_manager.get_response_stateful.call_args.kwargs["files"] == normalized.files
    cleanup.assert_awaited_once_with(normalized)


@pytest.mark.asyncio
async def test_gemini_webapi_adapter_passes_files_to_streaming_session(mocker):
    from pathlib import Path
    from types import SimpleNamespace

    provider = GeminiProvider()
    adapter = GeminiWebAPIAdapter(provider)

    mock_client = SimpleNamespace(client=SimpleNamespace(account_status=SimpleNamespace(name="AVAILABLE")))
    mock_registry = mocker.Mock()
    mock_manager = mocker.Mock()

    async def _stream():
        yield {"type": "chunk", "text_delta": "delta", "is_reused": False}

    mock_manager.get_streaming_response_stateful = mocker.Mock(return_value=_stream())
    mock_registry.get_session = mocker.AsyncMock(return_value=mock_manager)
    mock_registry.save_session_snapshot = mocker.AsyncMock()

    normalized = SimpleNamespace(
        messages=[{"role": "user", "content": "Summarize this document."}],
        files=[Path("/tmp/staged/invoice.pdf")],
        cleanup_dir=Path("/tmp/staged"),
    )

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=mock_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)
    cleanup = mocker.patch(
        "app.services.providers.gemini.webapi_adapter.cleanup_staged_files",
        mocker.AsyncMock(),
    )

    request = OpenAIChatRequest(
        messages=[
            {
                "role": "user",
                "content": [
                    _text_part("Summarize this document."),
                    _file_part(),
                ],
            }
        ],
        model="gemini-3-flash",
        stream=True,
    )
    request.messages = [{"role": "user", "content": "Summarize this document."}]
    object.__setattr__(request, "_normalized_openai_chat_messages", normalized)

    response = await adapter.chat_completions(request, "conv-123", True, "")

    from fastapi.responses import StreamingResponse

    assert isinstance(response, StreamingResponse)
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)

    assert chunks
    assert mock_manager.get_streaming_response_stateful.call_args.kwargs["files"] == normalized.files
    cleanup.assert_awaited_once_with(normalized)


@pytest.mark.asyncio
async def test_gemini_webapi_adapter_cleans_up_on_provider_error(mocker):
    from pathlib import Path
    from types import SimpleNamespace

    provider = GeminiProvider()
    adapter = GeminiWebAPIAdapter(provider)

    mock_client = SimpleNamespace(client=SimpleNamespace(account_status=SimpleNamespace(name="AVAILABLE")))
    mock_registry = mocker.Mock()
    mock_manager = mocker.Mock()
    mock_manager.get_response_stateful = mocker.AsyncMock(side_effect=RuntimeError("boom"))
    mock_registry.get_session = mocker.AsyncMock(return_value=mock_manager)

    normalized = SimpleNamespace(
        messages=[{"role": "user", "content": "Summarize this document."}],
        files=[Path("/tmp/staged/invoice.pdf")],
        cleanup_dir=Path("/tmp/staged"),
    )

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=mock_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)
    cleanup = mocker.patch(
        "app.services.providers.gemini.webapi_adapter.cleanup_staged_files",
        mocker.AsyncMock(),
    )

    request = OpenAIChatRequest(
        messages=[
            {
                "role": "user",
                "content": "Summarize this document.",
            }
        ],
        model="gemini-3-flash",
    )
    object.__setattr__(request, "_normalized_openai_chat_messages", normalized)

    with pytest.raises(HTTPException):
        await adapter.chat_completions(request, "conv-123", True, "")

    cleanup.assert_awaited_once_with(normalized)


@pytest.mark.asyncio
async def test_gemini_playwright_rejects_file_parts(mocker):
    provider = GeminiProvider()
    mock_adapter = mocker.Mock()
    mock_adapter.chat_completions = mocker.AsyncMock(return_value={"status": "ok"})
    mocker.patch.object(provider, "_get_adapter", return_value=mock_adapter)

    request = OpenAIChatRequest(
        messages=[
            {
                "role": "user",
                "content": [_text_part(), _file_part()],
            }
        ],
        model="playwright/gemini-3-flash",
    )

    with pytest.raises(HTTPException) as exc_info:
        await provider.chat_completions(request)

    assert exc_info.value.status_code == 400
    mock_adapter.chat_completions.assert_not_called()


@pytest.mark.asyncio
async def test_atlas_rejects_file_parts():
    provider = AtlasProvider()

    request = OpenAIChatRequest(
        messages=[
            {
                "role": "user",
                "content": [_text_part(), _file_part()],
            }
        ],
        model="atlas/MiniMax-M2",
        provider="atlas",
    )

    with pytest.raises(HTTPException) as exc_info:
        await provider.chat_completions(request)

    assert exc_info.value.status_code == 400
