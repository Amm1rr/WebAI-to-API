# src/app/config.py
import configparser
import logging
import os
import shutil

logger = logging.getLogger(__name__)

# Allow overriding config path via environment variable.
# In Docker, set CONFIG_PATH=/app/data/config.conf with a volume on /app/data.
DEFAULT_CONFIG_PATH = os.environ.get("CONFIG_PATH", "config.conf")


def _ensure_config_exists(config_file: str) -> None:
    """If config_file doesn't exist, copy from bundled default or create empty.

    Handles the Docker volume edge-case where Docker creates a *directory* at the
    config path when the host file doesn't exist yet.  We remove the empty directory
    and replace it with the proper file so no manual intervention is required.
    """
    if os.path.isdir(config_file):
        # Docker created a directory here instead of a file — remove it and continue.
        try:
            shutil.rmtree(config_file)
            logger.info(
                f"Removed directory at '{config_file}' (created by Docker volume mount); "
                "replacing with config file."
            )
        except Exception as e:
            logger.error(f"Could not remove directory '{config_file}': {e}")
            return

    if os.path.exists(config_file):
        return

    # Create parent directory if needed
    parent = os.path.dirname(config_file)
    if parent:
        os.makedirs(parent, exist_ok=True)

    # Copy bundled template as starting point
    bundled = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "config.conf")
    if os.path.isfile(bundled):
        shutil.copy2(bundled, config_file)
        logger.info(f"Copied bundled config to '{config_file}'")
    else:
        # Fallback: create an empty file so configparser has something to read
        open(config_file, "w", encoding="utf-8").close()
        logger.info(f"Created empty config file at '{config_file}'")


def load_config(config_file: str = None) -> configparser.ConfigParser:
    if config_file is None:
        config_file = DEFAULT_CONFIG_PATH
    _ensure_config_exists(config_file)
    config = configparser.ConfigParser()
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
        config["AI"] = {"default_model_gemini": "gemini-3.0-flash"}
    if "Proxy" not in config:
        config["Proxy"] = {"http_proxy": ""}
    if "Telegram" not in config:
        config["Telegram"] = {
            "enabled": "false",
            "bot_token": "",
            "chat_id": "",
            "cooldown_seconds": "60",
        }

    # Save changes to the configuration file, also with UTF-8 encoding.
    try:
        with open(config_file, "w", encoding="utf-8") as f:
            config.write(f)
        # logger.info("Configuration loaded/updated successfully.")
    except Exception as e:
        logger.error(f"Error writing to config file: {e}")

    return config


def write_config(config: configparser.ConfigParser, config_file: str = None) -> bool:
    """Write the current config state to disk."""
    if config_file is None:
        config_file = DEFAULT_CONFIG_PATH
    try:
        with open(config_file, "w", encoding="utf-8") as f:
            config.write(f)
        return True
    except Exception as e:
        logger.error(f"Error writing to config file: {e}")
        return False


# Load configuration globally
CONFIG = load_config()
