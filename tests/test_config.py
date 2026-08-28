import configparser
from pathlib import Path

import pytest

from app.config import load_config
from app.config_contract import load_effective_config


def write_config(tmp_path: Path, content: str) -> str:
    path = tmp_path / "config.conf"
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_gemini_extended_thinking_defaults_when_missing(tmp_path):
    config = load_config(write_config(tmp_path, "[Gemini]\nbackend = webapi\n"))

    assert config["Gemini"]["extended_thinking"] == "false"
    assert config.getboolean("Gemini", "extended_thinking") is False


def test_general_check_updates_defaults_to_true_when_section_missing(tmp_path):
    config = load_config(write_config(tmp_path, "[Gemini]\nbackend = webapi\n"))

    assert config["General"]["check_updates"] == "true"
    assert config.getboolean("General", "check_updates") is True


def test_general_check_updates_defaults_to_true_when_key_missing(tmp_path):
    config = load_config(write_config(tmp_path, "[General]\nkeep_me = value\n"))

    assert config["General"]["check_updates"] == "true"
    assert config["General"]["keep_me"] == "value"


@pytest.mark.parametrize(
    "value, expected",
    [("true", "true"), ("false", "false"), ("TRUE", "true"), ("False", "false"), ("  true  ", "true")],
)
def test_general_check_updates_boolean_values_are_validated_and_normalized(tmp_path, value, expected):
    config = load_config(write_config(tmp_path, f"[General]\ncheck_updates = {value}\n"))

    assert config["General"]["check_updates"] == expected


@pytest.mark.parametrize("value", ["yes", "no", "1", "0", "on", "off", "foo", ""])
def test_general_check_updates_invalid_value_fails_during_load(tmp_path, value):
    with pytest.raises(ValueError, match=r"Invalid General check_updates value"):
        load_config(write_config(tmp_path, f"[General]\ncheck_updates = {value}\n"))


def test_gemini_extended_thinking_key_defaults_when_section_exists(tmp_path):
    config = load_config(write_config(tmp_path, "[Gemini]\n"))

    assert config["Gemini"]["extended_thinking"] == "false"


@pytest.mark.parametrize(
    "value, expected",
    [("true", "true"), ("false", "false"), ("TRUE", "true"), ("False", "false"), ("  true  ", "true")],
)
def test_gemini_extended_thinking_boolean_values_are_validated_and_normalized(tmp_path, value, expected):
    config = load_config(write_config(tmp_path, f"[Gemini]\nextended_thinking = {value}\n"))

    assert config["Gemini"]["extended_thinking"] == expected


@pytest.mark.parametrize("value", ["yes", "no", "1", "0", "on", "off", "foo", ""])
def test_gemini_extended_thinking_invalid_value_fails_during_load(tmp_path, value):
    with pytest.raises(ValueError, match=r"Invalid Gemini extended_thinking value"):
        load_config(write_config(tmp_path, f"[Gemini]\nextended_thinking = {value}\n"))


def test_legacy_gemini_playwright_section_has_no_effect(tmp_path):
    config = load_config(
        write_config(
            tmp_path,
            "[Gemini]\nbackend = webapi\n\n[GeminiPlaywright]\nextended_thinking = true\n",
        )
    )

    assert config["Gemini"]["extended_thinking"] == "false"
    assert config["GeminiPlaywright"]["extended_thinking"] == "true"


def test_legacy_gemini_playwright_key_does_not_satisfy_gemini_default(tmp_path):
    config = load_config(
        write_config(
            tmp_path,
            "[Gemini]\nbackend = webapi\n\n[GeminiPlaywright]\nextended_thinking = true\n",
        )
    )

    assert config.getboolean("Gemini", "extended_thinking") is False


def test_legacy_gemini_playwright_value_is_not_validated(tmp_path):
    config = load_config(
        write_config(
            tmp_path,
            "[Gemini]\nbackend = webapi\n\n[GeminiPlaywright]\nextended_thinking = yes\n",
        )
    )

    assert config.getboolean("Gemini", "extended_thinking") is False


def test_gemini_backend_validation_remains_unchanged(tmp_path):
    with pytest.raises(ValueError, match="Invalid Gemini backend configured"):
        load_config(write_config(tmp_path, "[Gemini]\nbackend = invalid\n"))


def test_example_config_loads_through_shared_contract():
    config_path = Path(__file__).resolve().parents[1] / "config.conf.example"

    config = load_effective_config(str(config_path))

    assert config["Gemini"]["backend"] == "webapi"


def test_missing_config_still_loads_runtime_defaults(tmp_path):
    config = load_config(str(tmp_path / "missing.conf"))

    assert config["Browser"]["runtime"] == "playwright"
    assert config["Gemini"]["backend"] == "webapi"


@pytest.mark.parametrize(
    ("section", "option"),
    [("EnabledAI", "gemini"), ("Logging", "disable_access_logs")],
)
def test_startup_boolean_aliases_keep_configparser_semantics(tmp_path, section, option):
    config = load_config(write_config(tmp_path, f"[{section}]\n{option} = yes\n"))

    assert config.getboolean(section, option) is True


@pytest.mark.parametrize(
    ("section", "option"),
    [("EnabledAI", "gemini"), ("Logging", "disable_access_logs")],
)
def test_invalid_startup_boolean_fails_during_load(tmp_path, section, option):
    with pytest.raises(ValueError, match=rf"Invalid boolean value for {section}\.{option}"):
        load_config(write_config(tmp_path, f"[{section}]\n{option} = maybe\n"))


@pytest.mark.parametrize(
    "content",
    [
        "backend = webapi\n",
        "[Gemini]\nbackend = webapi\nbackend = playwright\n",
        "[Gemini]\nbackend = webapi\n\n[Gemini]\n",
    ],
)
def test_existing_malformed_or_duplicate_config_fails_closed(tmp_path, content):
    with pytest.raises(configparser.Error):
        load_config(write_config(tmp_path, content))


def test_invalid_utf8_config_fails_closed(tmp_path):
    config_path = tmp_path / "config.conf"
    config_path.write_bytes(b"[Gemini]\nbackend = \xff\n")

    with pytest.raises(UnicodeDecodeError):
        load_config(str(config_path))


def test_existing_directory_fails_closed(tmp_path):
    config_path = tmp_path / "config.conf"
    config_path.mkdir()

    with pytest.raises(IsADirectoryError):
        load_config(str(config_path))


def test_environment_overrides_remain_effective(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_STATE_DIR", "environment-auth")
    monkeypatch.setenv("PLAYWRIGHT_HEADLESS", "yes")

    config = load_config(
        write_config(
            tmp_path,
            "[Playwright]\nauth_state_dir = configured-auth\nheadless = false\n",
        )
    )

    assert config["Playwright"]["auth_state_dir"] == "environment-auth"
    assert config["Playwright"]["headless"] == "true"
