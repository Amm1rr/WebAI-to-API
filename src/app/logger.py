# src/app/logger.py
import logging

# Register custom levels in standard logging
logging.addLevelName(25, "SUCCESS")
logging.addLevelName(5, "TRACE")

class ColorLevelFormatter(logging.Formatter):
    """TTY-aware Formatter that colorizes only the levelname portion of log records."""

    COLORS = {
        "TRACE": "\033[95m",
        "DEBUG": "\033[96m",
        # INFO is intentionally omitted so it remains uncolored/default
        "SUCCESS": "\033[92m",
        "WARNING": "\033[93m",
        "ERROR": "\033[91m",
        "CRITICAL": "\033[1;91m",
    }
    RESET = "\033[0m"

    def __init__(self, fmt=None, datefmt=None, style="%"):
        super().__init__(fmt, datefmt, style)
        import sys
        self._use_color = hasattr(sys.stderr, "isatty") and sys.stderr.isatty()

    def format(self, record):
        orig_levelname = record.levelname
        if self._use_color and orig_levelname in self.COLORS:
            record.levelname = f"{self.COLORS[orig_levelname]}{orig_levelname}{self.RESET}"
            try:
                return super().format(record)
            finally:
                record.levelname = orig_levelname
        return super().format(record)


logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Apply ColorLevelFormatter to default handlers set by basicConfig
_default_formatter = ColorLevelFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
for handler in logging.getLogger().handlers:
    handler.setFormatter(_default_formatter)

logger = logging.getLogger("app")


def setup_logging(log_level: str, disable_access_logs: bool) -> None:
    """Configures the root logger, overrides verbose loggers, and bridges Loguru to standard logging."""
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    # Explicitly set root logger level (overrides fallback DEBUG level set on import)
    logging.getLogger().setLevel(numeric_level)

    # Ensure all current root handlers use ColorLevelFormatter
    formatter = ColorLevelFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    for handler in logging.getLogger().handlers:
        handler.setFormatter(formatter)

    # Prevent verbose logs from third-party libraries/HTTP clients from flooding the console
    logging.getLogger("httpx").setLevel(logging.DEBUG if log_level.upper() in ("DEBUG", "TRACE") else logging.WARNING)

    # Bridge Loguru (gemini_webapi) logs into standard logging via a function sink
    try:
        from loguru import logger as loguru_logger

        def loguru_sink(message):
            record = message.record
            name = record["extra"].get("name", record["name"])
            level_no = record["level"].no
            msg = record["message"]
            logging.getLogger(name).log(level_no, msg)

        loguru_logger.remove()  # Remove Loguru default handler
        loguru_logger.add(loguru_sink, level=0)  # Route all Loguru logs to Python logging
    except ImportError:
        pass

