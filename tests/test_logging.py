# File: tests/test_logging.py
import os
import logging
import configparser
from unittest import mock
import pytest

from app.config import resolve_logging_config
from app.logger import setup_logging

def test_default_log_level_is_info():
    # Clear environment variables to test default fallback
    with mock.patch.dict(os.environ, {}, clear=True):
        test_config = configparser.ConfigParser()
        level, disable_access = resolve_logging_config(None, False, config=test_config)
        assert level == "INFO"
        assert disable_access is False

def test_cli_overrides_env_and_config():
    with mock.patch.dict(os.environ, {"LOG_LEVEL": "WARNING", "DISABLE_ACCESS_LOGS": "true"}):
        test_config = configparser.ConfigParser()
        test_config.add_section("Logging")
        test_config.set("Logging", "level", "ERROR")
        test_config.set("Logging", "disable_access_logs", "true")
        
        # Explicit CLI value overrides
        level, disable_access = resolve_logging_config("DEBUG", False, config=test_config)
        assert level == "DEBUG"
        assert disable_access is True

def test_env_overrides_config():
    with mock.patch.dict(os.environ, {"LOG_LEVEL": "WARNING", "DISABLE_ACCESS_LOGS": "true"}):
        test_config = configparser.ConfigParser()
        test_config.add_section("Logging")
        test_config.set("Logging", "level", "ERROR")
        test_config.set("Logging", "disable_access_logs", "false")
        
        # With no CLI arg, Env takes precedence over Config
        level, disable_access = resolve_logging_config(None, False, config=test_config)
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


def test_color_level_formatter_tty():
    from app.logger import ColorLevelFormatter
    # Mock sys.stderr.isatty to return True
    with mock.patch("sys.stderr.isatty", return_value=True):
        formatter = ColorLevelFormatter("%(levelname)s - %(message)s")
        
        # Verify SUCCESS gets green color
        record_success = logging.LogRecord(
            name="test", level=25, pathname="test.py", lineno=1,
            msg="test success message", args=(), exc_info=None
        )
        formatted_success = formatter.format(record_success)
        assert "\033[92mSUCCESS\033[0m" in formatted_success
        
        # Verify DEBUG gets cyan color
        record_debug = logging.LogRecord(
            name="test", level=logging.DEBUG, pathname="test.py", lineno=1,
            msg="test debug message", args=(), exc_info=None
        )
        formatted_debug = formatter.format(record_debug)
        assert "\033[96mDEBUG\033[0m" in formatted_debug

        # Verify TRACE gets magenta color
        record_trace = logging.LogRecord(
            name="test", level=5, pathname="test.py", lineno=1,
            msg="test trace message", args=(), exc_info=None
        )
        formatted_trace = formatter.format(record_trace)
        assert "\033[95mTRACE\033[0m" in formatted_trace

        # Verify INFO remains uncolored
        record_info = logging.LogRecord(
            name="test", level=logging.INFO, pathname="test.py", lineno=1,
            msg="test info message", args=(), exc_info=None
        )
        formatted_info = formatter.format(record_info)
        assert "\033" not in formatted_info
        assert "INFO" in formatted_info


def test_color_level_formatter_non_tty():
    from app.logger import ColorLevelFormatter
    # Mock sys.stderr.isatty to return False
    with mock.patch("sys.stderr.isatty", return_value=False):
        formatter = ColorLevelFormatter("%(levelname)s - %(message)s")
        
        # Verify SUCCESS has no color codes
        record_success = logging.LogRecord(
            name="test", level=25, pathname="test.py", lineno=1,
            msg="test success message", args=(), exc_info=None
        )
        formatted_success = formatter.format(record_success)
        assert "\033" not in formatted_success
        assert "SUCCESS" in formatted_success

        # Verify DEBUG has no color codes
        record_debug = logging.LogRecord(
            name="test", level=logging.DEBUG, pathname="test.py", lineno=1,
            msg="test debug message", args=(), exc_info=None
        )
        formatted_debug = formatter.format(record_debug)
        assert "\033" not in formatted_debug
        assert "DEBUG" in formatted_debug


def test_setup_logging_applies_formatter():
    from app.logger import ColorLevelFormatter
    root_logger = logging.getLogger()
    mock_handler = mock.Mock(spec=logging.Handler)
    root_logger.addHandler(mock_handler)
    try:
        setup_logging("INFO", False)
        mock_handler.setFormatter.assert_called()
        args, _ = mock_handler.setFormatter.call_args
        assert isinstance(args[0], ColorLevelFormatter)
    finally:
        root_logger.removeHandler(mock_handler)
