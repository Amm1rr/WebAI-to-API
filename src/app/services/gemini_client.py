# src/app/services/gemini_client.py
from models.gemini import MyGeminiClient
from app.config import CONFIG
from app.logger import logger
from app.utils.browser import get_cookie_from_browser

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
                _gemini_client = MyGeminiClient(gemini_cookie_1PSID, gemini_cookie_1PSIDTS)
                await _gemini_client.init()
                logger.info("Gemini client initialized successfully.")
                return True
            else:
                logger.warning("Gemini cookies not found. Gemini API will not be available.")
                return False
        except Exception as e:
            logger.error(f"Failed to initialize Gemini client: {e}", exc_info=True)
            _gemini_client = None
            return False
    else:
        logger.info("Gemini client is disabled.")
        return False

async def close_gemini_client():
    """
    Close the Gemini client when the application shuts down.
    """
    global _gemini_client
    if _gemini_client:
        await _gemini_client.close()
        logger.info("Gemini client closed successfully.")

def get_gemini_client():
    return _gemini_client