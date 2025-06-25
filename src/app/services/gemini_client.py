# src/app/services/gemini_client.py
from models.gemini import MyGeminiClient
from app.config import CONFIG
from app.logger import logger
from app.utils.browser import get_cookie_from_browser

# Import the specific exception to handle it gracefully
from gemini_webapi.exceptions import AuthError

# Global variable to store the Gemini client instance
_gemini_client = None

async def init_gemini_client() -> bool:
    """
    Initialize and set up the Gemini client based on the configuration.
    Returns True on success, False on failure.
    """
    global _gemini_client
    if CONFIG.getboolean("EnabledAI", "gemini", fallback=True):
        try:
            gemini_cookie_1PSID = CONFIG["Cookies"].get("gemini_cookie_1PSID")
            gemini_cookie_1PSIDTS = CONFIG["Cookies"].get("gemini_cookie_1PSIDTS")
            if not gemini_cookie_1PSID or not gemini_cookie_1PSIDTS:
                cookies = get_cookie_from_browser("gemini")
                if cookies:
                    gemini_cookie_1PSID, gemini_cookie_1PSIDTS = cookies
            
            if gemini_cookie_1PSID and gemini_cookie_1PSIDTS:
                _gemini_client = MyGeminiClient(secure_1psid=gemini_cookie_1PSID, secure_1psidts=gemini_cookie_1PSIDTS)
                await _gemini_client.init()
                # logger.info("Gemini client initialized successfully.")
                return True
            else:
                logger.warning("Gemini cookies not found. Gemini API will not be available.")
                return False

        # FIX: Catch the specific AuthError for better logging and error handling.
        except AuthError as e:
            logger.error(
                f"Gemini authentication or connection failed: {e}. "
                "This could be due to expired cookies or a temporary network issue with Google's servers (like a 502 error)."
            )
            _gemini_client = None
            return False
            
        # Keep a general exception handler for any other unexpected issues.
        except Exception as e:
            logger.error(f"An unexpected error occurred while initializing Gemini client: {e}", exc_info=True)
            _gemini_client = None
            return False
    else:
        logger.info("Gemini client is disabled.")
        return False


def get_gemini_client():
    """
    Returns the initialized Gemini client instance.
    """
    return _gemini_client

