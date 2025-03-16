# src/app/utils/browser.py
import logging
import browser_cookie3
from typing import Optional, Literal
from app.config import CONFIG

logger = logging.getLogger(__name__)

def get_cookie_from_browser(service: Literal["gemini"]) -> Optional[tuple]:
    browser_name = CONFIG["Browser"].get("name", "firefox").lower()
    logger.info(f"Attempting to get cookies from browser: {browser_name} for service: {service}")
    try:
        if browser_name == "firefox":
            cookies = browser_cookie3.firefox()
        elif browser_name == "chrome":
            cookies = browser_cookie3.chrome()
        elif browser_name == "brave":
            cookies = browser_cookie3.brave()
        elif browser_name == "edge":
            cookies = browser_cookie3.edge()
        elif browser_name == "safari":
            cookies = browser_cookie3.safari()
        else:
            raise ValueError(f"Unsupported browser: {browser_name}")
        logger.info(f"Successfully retrieved cookies from {browser_name}")
    except ValueError as ve:
        logger.error(f"Unsupported browser: {browser_name} - {ve}")
        return None
    except browser_cookie3.BrowserCookieError as bce:
        logger.error(f"Error retrieving cookies from {browser_name}: {bce}")
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred while retrieving cookies from {browser_name}: {e}", exc_info=True)
        return None

    if service == "gemini":
        logger.info("Looking for Gemini cookies (__Secure-1PSID and __Secure-1PSIDTS)...")
        secure_1psid = None
        secure_1psidts = None
        for cookie in cookies:
            if cookie.name == "__Secure-1PSID" and "google" in cookie.domain:
                secure_1psid = cookie.value
                logger.info(f"Found __Secure-1PSID: {secure_1psid}")
            elif cookie.name == "__Secure-1PSIDTS" and "google" in cookie.domain:
                secure_1psidts = cookie.value
                logger.info(f"Found __Secure-1PSIDTS: {secure_1psidts}")
        if secure_1psid and secure_1psidts:
            logger.info("Both Gemini cookies found.")
            return secure_1psid, secure_1psidts
        else:
            logger.warning("Gemini cookies not found or incomplete.")
            return None
    else:
        logger.warning(f"Unsupported service: {service}")
        return None
