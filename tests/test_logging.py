# File: tests/test_logging.py
import os
import logging
from unittest import mock
import pytest

from run import resolve_logging_config, setup_logging

def test_default_log_level_is_info():
    # Clear environment variables to test default fallback
    with mock.patch.dict(os.environ, {}, clear=True):
        with mock.patch("app.config.CONFIG.has_section", return_value=False):
            level, disable_access = resolve_logging_config(None, False)
            assert level == "INFO"
            assert disable_access is False

def test_cli_overrides_env_and_config():
    with mock.patch.dict(os.environ, {"LOG_LEVEL": "WARNING", "DISABLE_ACCESS_LOGS": "true"}):
        with mock.patch("app.config.CONFIG.has_section", return_value=True):
            with mock.patch("app.config.CONFIG.get", return_value="ERROR"):
                with mock.patch("app.config.CONFIG.getboolean", return_value=True):
                    # Explicit CLI value overrides
                    level, disable_access = resolve_logging_config("DEBUG", False)
                    assert level == "DEBUG"
                    # Note: resolved_disable_access resolves to True because (CLI: False OR Env: True OR Config: True) is True
                    assert disable_access is True

def test_env_overrides_config():
    with mock.patch.dict(os.environ, {"LOG_LEVEL": "WARNING", "DISABLE_ACCESS_LOGS": "true"}):
        with mock.patch("app.config.CONFIG.has_section", return_value=True):
            with mock.patch("app.config.CONFIG.get", return_value="ERROR"):
                with mock.patch("app.config.CONFIG.getboolean", return_value=False):
                    # With no CLI arg, Env takes precedence over Config
                    level, disable_access = resolve_logging_config(None, False)
                    assert level == "WARNING"
                    assert disable_access is True

def test_loguru_debug_suppressed_at_info():
    # Set logging to INFO
    setup_logging("INFO", False)
    
    # Assert that gemini_webapi logger is NOT enabled for DEBUG
    assert not logging.getLogger("gemini_webapi").isEnabledFor(logging.DEBUG)
    
    # Set logging to DEBUG
    setup_logging("DEBUG", False)
    
    # Assert that gemini_webapi logger IS enabled for DEBUG
    assert logging.getLogger("gemini_webapi").isEnabledFor(logging.DEBUG)

def test_fallback_logger_works_on_import():
    # Ensure standard fallback logger works for tests and utility imports
    from app.logger import logger
    assert logger.name == "app"
    assert len(logging.getLogger().handlers) > 0

def test_custom_logging_levels():
    # Verify SUCCESS (25) and TRACE (5) levels are registered in standard logging
    assert logging.getLevelName(25) == "SUCCESS"
    assert logging.getLevelName(5) == "TRACE"

