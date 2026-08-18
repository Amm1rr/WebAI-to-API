from pathlib import Path

import pytest

from app.config import load_config


def write_config(tmp_path: Path, content: str) -> str:
    path = tmp_path / "config.conf"
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_gemini_playwright_section_defaults_when_missing(tmp_path):
    config = load_config(write_config(tmp_path, "[Gemini]\nbackend = webapi\n"))

    assert config["GeminiPlaywright"]["extended_thinking"] == "false"
    assert config.getboolean("GeminiPlaywright", "extended_thinking") is False


def test_gemini_playwright_key_defaults_when_section_exists(tmp_path):
    config = load_config(write_config(tmp_path, "[GeminiPlaywright]\n"))

    assert config["GeminiPlaywright"]["extended_thinking"] == "false"


@pytest.mark.parametrize(
    "value, expected",
    [("true", "true"), ("false", "false"), ("TRUE", "true"), ("False", "false")],
)
def test_gemini_playwright_boolean_values_are_validated_and_normalized(tmp_path, value, expected):
    config = load_config(write_config(tmp_path, f"[GeminiPlaywright]\nextended_thinking = {value}\n"))

    assert config["GeminiPlaywright"]["extended_thinking"] == expected


@pytest.mark.parametrize("value", ["yes", "no", "1", "0", "on", "off", "foo", ""])
def test_gemini_playwright_invalid_value_fails_during_load(tmp_path, value):
    with pytest.raises(ValueError, match=r"Invalid GeminiPlaywright extended_thinking value"):
        load_config(write_config(tmp_path, f"[GeminiPlaywright]\nextended_thinking = {value}\n"))


def test_gemini_backend_validation_remains_unchanged(tmp_path):
    with pytest.raises(ValueError, match="Invalid Gemini backend configured"):
        load_config(write_config(tmp_path, "[Gemini]\nbackend = invalid\n"))
