import configparser
import os

from app.utils.runtime_paths import get_default_auth_state_dir, resolve_auth_state_dir


def normalize_strict_boolean(raw_value: str, setting_name: str) -> str:
    """Return canonical true/false, rejecting ConfigParser's wider boolean set."""
    normalized = raw_value.strip().lower()
    if normalized not in ("true", "false"):
        raise ValueError(
            f"Invalid {setting_name} value '{raw_value}'. Expected true/false."
        )
    return normalized


def _parse_config(config_file: str) -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    config.optionxform = str  # Preserve case for cookie names.
    try:
        with open(config_file, encoding="utf-8") as handle:
            config.read_file(handle, source=config_file)
    except FileNotFoundError:
        pass
    return config


def _apply_defaults(config: configparser.ConfigParser) -> None:
    if "Browser" not in config:
        config["Browser"] = {"name": "chrome"}
    if "runtime" not in config["Browser"]:
        config["Browser"]["runtime"] = "playwright"
    if "Cookies" not in config:
        config["Cookies"] = {}
    if "Proxy" not in config:
        config["Proxy"] = {"http_proxy": ""}
    if "General" not in config:
        config["General"] = {"check_updates": "true"}
    elif "check_updates" not in config["General"]:
        config["General"]["check_updates"] = "true"
    if "Playwright" not in config:
        config["Playwright"] = {
            "headless": "false",
            "max_concurrent_pages": "5",
            "max_total_tabs": "50",
            "max_persistent_conversations": "20",
            "navigation_timeout": "30000",
            "ui_wait_timeout": "15000",
            "idle_conversation_timeout": "900",
            "lease_timeout": "180",
            "chunk_timeout": "90",
            "total_request_timeout": "120",
            "auth_state_dir": get_default_auth_state_dir(),
            "auth_lock_backend": "in_memory",
        }
    else:
        if "auth_state_dir" not in config["Playwright"]:
            config["Playwright"]["auth_state_dir"] = get_default_auth_state_dir()
        if "auth_lock_backend" not in config["Playwright"]:
            config["Playwright"]["auth_lock_backend"] = "in_memory"

    legacy_gemini_model = config.get("AI", "default_model_gemini", fallback=None)
    if "Gemini" not in config:
        config["Gemini"] = {
            "backend": "webapi",
            "default_model": legacy_gemini_model or "gemini-3-flash",
            "extended_thinking": "false",
        }
    else:
        if "backend" not in config["Gemini"]:
            config["Gemini"]["backend"] = "webapi"
        if "default_model" not in config["Gemini"]:
            config["Gemini"]["default_model"] = legacy_gemini_model or "gemini-3-flash"
        if "extended_thinking" not in config["Gemini"]:
            config["Gemini"]["extended_thinking"] = "false"


def _apply_environment_overrides(config: configparser.ConfigParser) -> None:
    config["Playwright"]["auth_state_dir"] = resolve_auth_state_dir(
        config["Playwright"].get("auth_state_dir")
    )
    env_headless = os.environ.get("PLAYWRIGHT_HEADLESS")
    if env_headless is not None:
        config["Playwright"]["headless"] = (
            "true" if env_headless.strip().lower() in ("1", "true", "yes", "on") else "false"
        )


def _validate_configparser_boolean(
    config: configparser.ConfigParser, section: str, option: str
) -> None:
    if not config.has_option(section, option):
        return
    value = config.get(section, option)
    try:
        config.getboolean(section, option)
    except ValueError as error:
        raise ValueError(
            f"Invalid boolean value for {section}.{option}: '{value}'."
        ) from error


def _validate_config(config: configparser.ConfigParser) -> None:
    gemini_backend = config["Gemini"].get("backend", "webapi").lower().strip()
    if gemini_backend not in ("webapi", "playwright"):
        raise ValueError(
            f"Invalid Gemini backend configured: '{gemini_backend}'. "
            "Supported values: 'webapi', 'playwright'."
        )
    config["Gemini"]["backend"] = gemini_backend
    config["Gemini"]["extended_thinking"] = normalize_strict_boolean(
        config["Gemini"].get("extended_thinking", "false"),
        "Gemini extended_thinking",
    )
    config["General"]["check_updates"] = normalize_strict_boolean(
        config["General"].get("check_updates", "true"),
        "General check_updates",
    )
    _validate_configparser_boolean(config, "EnabledAI", "gemini")
    _validate_configparser_boolean(config, "Logging", "disable_access_logs")


def load_effective_config(config_file: str = "config.conf") -> configparser.ConfigParser:
    """Parse, normalize, and validate runtime configuration without side effects."""
    config = _parse_config(config_file)
    _apply_defaults(config)
    _apply_environment_overrides(config)
    _validate_config(config)
    return config
