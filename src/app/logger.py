# src/app/logger.py
import logging

# Register custom levels in standard logging
logging.addLevelName(25, "SUCCESS")
logging.addLevelName(5, "TRACE")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("app")


def setup_logging(log_level: str, disable_access_logs: bool) -> None:
    """Configures the root logger, overrides verbose loggers, and bridges Loguru to standard logging."""
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    # Explicitly set root logger level (overrides fallback DEBUG level set on import)
    logging.getLogger().setLevel(numeric_level)

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

