# src/app/config.py
import configparser
import logging

logger = logging.getLogger(__name__)

def load_config(config_file: str = "config.conf") -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    config.read(config_file)

    if "Browser" not in config:
        config["Browser"] = {"name": "chrome"}
    if "Cookies" not in config:
        config["Cookies"] = {}

    # Save changes to the configuration file
    with open(config_file, "w") as f:
        config.write(f)
    # logger.info("Configuration loaded and updated.")
    return config

# Load configuration globally
CONFIG = load_config()
