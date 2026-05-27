import pytest
import json
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
