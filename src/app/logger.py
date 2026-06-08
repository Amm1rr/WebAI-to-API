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
