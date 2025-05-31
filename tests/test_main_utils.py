"""
Unit tests for utility functions in webaitoapi.main.
"""
import time
import pytest
from webaitoapi.main import convert_to_openai_format

def test_convert_to_openai_format_non_streaming():
    """
    Test convert_to_openai_format for non-streaming responses.
    """
    content = "This is a test response."
    model_name = "test-model"
    timestamp_before = int(time.time())
    result = convert_to_openai_format(response_content=content, model_name=model_name, stream=False)
    timestamp_after = int(time.time())

    assert result["object"] == "chat.completion"
    assert result["model"] == model_name
    assert result["id"].startswith("chatcmpl-ns-")
    assert timestamp_before <= result["created"] <= timestamp_after

    assert len(result["choices"]) == 1
    choice = result["choices"][0]
    assert choice["index"] == 0
    assert choice["finish_reason"] == "stop"
    assert choice["message"]["role"] == "assistant"
    assert choice["message"]["content"] == content

    assert "delta" not in choice # Should not have delta for non-streaming

    assert "usage" in result
    assert result["usage"]["prompt_tokens"] == 0 # Placeholder
    assert result["usage"]["completion_tokens"] == 0 # Placeholder
    assert result["usage"]["total_tokens"] == 0 # Placeholder

def test_convert_to_openai_format_streaming():
    """
    Test convert_to_openai_format for streaming responses (single chunk).
    """
    content = "This is a streaming chunk."
    model_name = "stream-model"
    timestamp_before = int(time.time())
    # For streaming, finish_reason might be None for intermediate chunks, or "stop" for the last.
    # convert_to_openai_format itself defaults finish_reason to None for stream=True unless specified.
    result = convert_to_openai_format(response_content=content, model_name=model_name, stream=True)
    timestamp_after = int(time.time())

    assert result["object"] == "chat.completion.chunk"
    assert result["model"] == model_name
    assert result["id"].startswith("chatcmpl-s-")
    assert timestamp_before <= result["created"] <= timestamp_after

    assert len(result["choices"]) == 1
    choice = result["choices"][0]
    assert choice["index"] == 0
    assert choice["finish_reason"] is None # Default for a generic chunk

    assert choice["delta"]["role"] == "assistant"
    assert choice["delta"]["content"] == content

    assert "message" not in choice # Should not have message for streaming
    assert "usage" not in result # Should not have usage for streaming chunks

def test_convert_to_openai_format_streaming_with_finish_reason():
    """
    Test convert_to_openai_format for a streaming response's last chunk with a finish_reason.
    """
    content = "Final streaming chunk."
    model_name = "stream-model-final"
    finish_reason = "stop"
    result = convert_to_openai_format(
        response_content=content,
        model_name=model_name,
        stream=True,
        finish_reason_val=finish_reason
    )

    assert result["object"] == "chat.completion.chunk"
    assert len(result["choices"]) == 1
    choice = result["choices"][0]
    assert choice["finish_reason"] == finish_reason
    assert choice["delta"]["content"] == content

def test_convert_to_openai_format_empty_content_non_streaming():
    """
    Test convert_to_openai_format with empty content for non-streaming.
    """
    content = ""
    model_name = "empty-model"
    result = convert_to_openai_format(response_content=content, model_name=model_name, stream=False)

    assert result["choices"][0]["message"]["content"] == ""
    assert result["usage"] is not None # Usage should still be present

def test_convert_to_openai_format_empty_content_streaming():
    """
    Test convert_to_openai_format with empty content for streaming.
    """
    content = ""
    model_name = "empty-stream-model"
    result = convert_to_openai_format(response_content=content, model_name=model_name, stream=True)

    assert result["choices"][0]["delta"]["content"] == ""
    assert "usage" not in result

def test_convert_to_openai_format_custom_id_and_finish_reason_non_streaming():
    """
    Test convert_to_openai_format for non-streaming with custom ID and finish_reason.
    """
    content = "Test content"
    model_name = "custom-model"
    custom_id = "my-custom-id-123"
    custom_finish_reason = "length"

    result = convert_to_openai_format(
        response_content=content,
        model_name=model_name,
        stream=False,
        response_id_val=custom_id,
        finish_reason_val=custom_finish_reason
    )

    assert result["id"] == custom_id
    assert result["choices"][0]["finish_reason"] == custom_finish_reason
    assert result["choices"][0]["message"]["content"] == content
    assert result["object"] == "chat.completion"

# More tests could be added for various combinations of inputs.
# For example, testing specific model names, different unicode characters in content, etc.
# The current set covers the main structural differences based on parameters.
