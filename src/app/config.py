# src/app/config.py
import configparser
import logging

from app.env import load_local_env

logger = logging.getLogger(__name__)

load_local_env()


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
    if "AI" not in config:
        config["AI"] = {"default_model_gemini": "gemini-3-flash"}
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
            "auth_state_dir": "auth_state"
        }
    else:
        if "auth_state_dir" not in config["Playwright"]:
            config["Playwright"]["auth_state_dir"] = "auth_state"

    # Save changes to the configuration file, also with UTF-8 encoding.
    try:
        import asyncio
        from app.utils.config_utils import save_config_atomic
        try:
            # We try to use the atomic save if an event loop is running.
            loop = asyncio.get_running_loop()
            if loop.is_running():
                # We can't easily wait for it here without making load_config async,
                # which would be a huge breaking change. 
                # For initial load, blocking I/O is actually acceptable as it happens during startup.
                # However, for consistency, we'll keep the blocking write here but use the same logic.
                with open(config_file, "w", encoding="utf-8") as f:
                    config.write(f)
        except RuntimeError:
            # No event loop, normal blocking write.
            with open(config_file, "w", encoding="utf-8") as f:
                config.write(f)
    except Exception as e:
        logger.error(f"Error writing to config file: {e}")

    import os
    env_auth_state_dir = os.environ.get("AUTH_STATE_DIR")
    if env_auth_state_dir:
        config["Playwright"]["auth_state_dir"] = env_auth_state_dir

    env_headless = os.environ.get("PLAYWRIGHT_HEADLESS")
    if env_headless is not None:
        is_headless = env_headless.strip().lower() in ("1", "true", "yes", "on")
        config["Playwright"]["headless"] = "true" if is_headless else "false"

    return config


# Load configuration globally
CONFIG = load_config()
