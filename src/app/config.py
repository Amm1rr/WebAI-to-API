# src/app/config.py
import configparser
import os

from app.config_contract import load_effective_config, normalize_strict_boolean
from app.env import load_local_env
from app.utils.runtime_paths import (
    get_default_auth_state_dir,
    get_default_conversation_snapshot_db,
    get_default_playwright_cache_dir,
    get_runtime_dir,
    resolve_auth_state_dir,
    resolve_conversation_snapshot_db,
)

load_local_env()


def load_config(config_file: str = "config.conf"):
    return load_effective_config(config_file)


# Load configuration globally
CONFIG = load_config()


def resolve_logging_config(
    cli_log_level: str | None,
    cli_disable_access_logs: bool,
    config: configparser.ConfigParser | None = None,
) -> tuple[str, bool]:
    """Resolves log level and access log settings based on CLI, env, and config precedence."""
    config = config or CONFIG

    # 1. Resolve log level precedence: explicit CLI > LOG_LEVEL env > config.conf > INFO
    env_log_level = os.environ.get("LOG_LEVEL")
    conf_log_level = config.get("Logging", "level", fallback=None) if config.has_section("Logging") else None
    resolved_level = cli_log_level or env_log_level or conf_log_level or "INFO"

    # 2. Resolve access logs precedence: explicit CLI --disable-access-logs > DISABLE_ACCESS_LOGS env > config.conf > false
    env_disable_access = os.environ.get("DISABLE_ACCESS_LOGS", "false").lower() in ("true", "1", "yes", "on")
    conf_disable_access = config.getboolean("Logging", "disable_access_logs", fallback=False) if config.has_section("Logging") else False
    resolved_disable_access = cli_disable_access_logs or env_disable_access or conf_disable_access

    return resolved_level, resolved_disable_access
