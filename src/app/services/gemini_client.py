# src/app/services/gemini_client.py
from models.gemini import MyGeminiClient
from app.config import CONFIG
from app.logger import logger
from app.utils.browser import get_cookie_from_browser

# Import the specific exception to handle it gracefully
from gemini_webapi.exceptions import AuthError


class GeminiClientNotInitializedError(Exception):
    """Raised when the Gemini client is not initialized or initialization failed."""
    pass


# Global variable to store the Gemini client instance
_gemini_client = None
_initialization_error = None

async def init_gemini_client() -> bool:
    """
    Initialize and set up the Gemini client based on the configuration.
    Returns True on success, False on failure.
    """
    global _gemini_client, _initialization_error
    _initialization_error = None

    if CONFIG.getboolean("EnabledAI", "gemini", fallback=True):
        try:
            gemini_cookie_1PSID = CONFIG["Cookies"].get("gemini_cookie_1PSID")
            gemini_cookie_1PSIDTS = CONFIG["Cookies"].get("gemini_cookie_1PSIDTS")
            gemini_proxy = CONFIG["Proxy"].get("http_proxy")

            if not gemini_cookie_1PSID or not gemini_cookie_1PSIDTS:
                cookies = get_cookie_from_browser("gemini")
                if cookies:
                    gemini_cookie_1PSID, gemini_cookie_1PSIDTS = cookies

            if gemini_proxy == "":
                gemini_proxy = None

            if gemini_cookie_1PSID and gemini_cookie_1PSIDTS:
                _gemini_client = MyGeminiClient(secure_1psid=gemini_cookie_1PSID, secure_1psidts=gemini_cookie_1PSIDTS, proxy=gemini_proxy)
                await _gemini_client.init()
                logger.info("Gemini client initialized successfully.")
                return True
            else:
                error_msg = "Gemini cookies not found. Please provide cookies in config.conf or ensure browser is logged in."
                logger.error(error_msg)
                _initialization_error = error_msg
                return False

        except AuthError as e:
            error_msg = f"Gemini authentication failed: {e}. This usually means cookies are expired or invalid."
            logger.error(error_msg)
            _gemini_client = None
            _initialization_error = error_msg
            return False

        except Exception as e:
            error_msg = f"Unexpected error initializing Gemini client: {e}"
            logger.error(error_msg, exc_info=True)
            _gemini_client = None
            _initialization_error = error_msg
            return False
    else:
        error_msg = "Gemini client is disabled in config."
        logger.info(error_msg)
        _initialization_error = error_msg
        return False


def get_gemini_client():
    """
    Returns the initialized Gemini client instance.

    Raises:
        GeminiClientNotInitializedError: If the client is not initialized.
    """
    if _gemini_client is None:
        error_detail = _initialization_error or "Gemini client was not initialized. Check logs for details."
        raise GeminiClientNotInitializedError(error_detail)
    return _gemini_client

