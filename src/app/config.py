# src/app/config.py
import configparser
import logging
import os

from app.env import load_local_env

logger = logging.getLogger(__name__)

load_local_env()


def get_runtime_dir() -> str:
    return os.environ.get("RUNTIME_DIR", "runtime")


def get_default_auth_state_dir() -> str:
    return os.path.join(get_runtime_dir(), "auth")


def get_default_conversation_snapshot_db() -> str:
    return os.path.join(get_runtime_dir(), "conversations", "conversation_snapshots.db")


def get_default_playwright_cache_dir() -> str:
    return os.path.join(get_runtime_dir(), "cache", "playwright")


def load_config(config_file: str = "config.conf") -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    config.optionxform = str  # Preserve case for cookie names
    try:
        # FIX: Explicitly specify UTF-8 encoding to prevent UnicodeDecodeError on Windows.
        # This is the standard and most compatible way to handle text files across platforms.
        config.read(config_file, encoding="utf-8")
    except FileNotFoundError:
        logger.warning(
            f"Config file '{config_file}' not found. Creating a default one."
        )
    except Exception as e:
        logger.error(f"Error reading config file: {e}")

    # Set default sections and values if they don't exist
    if "Browser" not in config:
        config["Browser"] = {"name": "chrome"}
    if "Cookies" not in config:
        config["Cookies"] = {}
    if "Proxy" not in config:
        config["Proxy"] = {"http_proxy": ""}
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
            "auth_lock_backend": "in_memory"
        }
    else:
        if "auth_state_dir" not in config["Playwright"]:
            config["Playwright"]["auth_state_dir"] = get_default_auth_state_dir()
        if "auth_lock_backend" not in config["Playwright"]:
            config["Playwright"]["auth_lock_backend"] = "in_memory"



    env_auth_state_dir = os.environ.get("AUTH_STATE_DIR")
    if env_auth_state_dir:
        config["Playwright"]["auth_state_dir"] = env_auth_state_dir

    env_headless = os.environ.get("PLAYWRIGHT_HEADLESS")
    if env_headless is not None:
        is_headless = env_headless.strip().lower() in ("1", "true", "yes", "on")
        config["Playwright"]["headless"] = "true" if is_headless else "false"

    # Resolve Gemini default model with legacy fallback
    # Precedence: [Gemini] default_model > [AI] default_model_gemini > hardcoded default
    legacy_gemini_model = config.get("AI", "default_model_gemini", fallback=None)
    
    if "Gemini" not in config:
        config["Gemini"] = {
            "backend": "webapi",
            "default_model": legacy_gemini_model or "gemini-3-flash"
        }
    else:
        if "backend" not in config["Gemini"]:
            config["Gemini"]["backend"] = "webapi"
        if "default_model" not in config["Gemini"]:
            config["Gemini"]["default_model"] = legacy_gemini_model or "gemini-3-flash"
    
    # Validate Gemini backend
    gemini_backend = config["Gemini"].get("backend", "webapi").lower().strip()
    if gemini_backend not in ("webapi", "playwright"):
        raise ValueError(f"Invalid Gemini backend configured: '{gemini_backend}'. Supported values: 'webapi', 'playwright'.")
    
    config["Gemini"]["backend"] = gemini_backend

    return config


# Load configuration globally
CONFIG = load_config()
