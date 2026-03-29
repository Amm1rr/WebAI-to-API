# src/app/services/gemini_client.py
import asyncio
from models.gemini import MyGeminiClient
from app.config import CONFIG, write_config
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
_error_code = None  # "auth_expired", "no_cookies", "network", "disabled", "unknown"
_persist_task: asyncio.Task = None  # Background task for persisting rotated cookies

async def init_gemini_client() -> bool:
    """
    Initialize and set up the Gemini client based on the configuration.
    Returns True on success, False on failure.
    """
    global _gemini_client, _initialization_error, _error_code
    _initialization_error = None
    _error_code = None

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
                _error_code = "no_cookies"
                _initialization_error = "Gemini cookies not found."
                logger.error(_initialization_error)
                return False

        except AuthError as e:
            _error_code = "auth_expired"
            _initialization_error = str(e)
            logger.error(f"Gemini authentication failed: {e}")
            _gemini_client = None
            return False

        except (ConnectionError, OSError, TimeoutError) as e:
            _error_code = "network"
            _initialization_error = str(e)
            logger.error(f"Network error initializing Gemini client: {e}")
            _gemini_client = None
            return False

        except Exception as e:
            _error_code = "unknown"
            _initialization_error = str(e)
            logger.error(f"Unexpected error initializing Gemini client: {e}", exc_info=True)
            _gemini_client = None
            return False
    else:
        _error_code = "disabled"
        _initialization_error = "Gemini client is disabled in config."
        logger.info(_initialization_error)
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


def get_client_status() -> dict:
    """Return the current status of the Gemini client for the admin UI."""
    return {
        "initialized": _gemini_client is not None,
        "error": _initialization_error,
        "error_code": _error_code,
    }


async def _persist_cookies_loop():
    """
    Background task that watches for cookie rotation by gemini-webapi's auto_refresh
    mechanism and persists any updated values back to config.conf.

    The library rotates __Secure-1PSIDTS every ~9 minutes in-memory only.
    Without this task, a server restart would reload the original (expired) cookies.
    """
    # Wait one full refresh cycle before first check so the library has time to rotate
    await asyncio.sleep(600)
    while True:
        try:
            if _gemini_client is not None:
                # Access the underlying WebGeminiClient cookies dict
                client_cookies = _gemini_client.client.cookies
                new_1psid = client_cookies.get("__Secure-1PSID")
                new_1psidts = client_cookies.get("__Secure-1PSIDTS")

                current_1psid = CONFIG["Cookies"].get("gemini_cookie_1PSID", "")
                current_1psidts = CONFIG["Cookies"].get("gemini_cookie_1PSIDTS", "")

                changed = False
                if new_1psid and new_1psid != current_1psid:
                    CONFIG["Cookies"]["gemini_cookie_1PSID"] = new_1psid
                    changed = True
                    logger.info("__Secure-1PSID rotated — will persist to config.")
                if new_1psidts and new_1psidts != current_1psidts:
                    CONFIG["Cookies"]["gemini_cookie_1PSIDTS"] = new_1psidts
                    changed = True
                    logger.info("__Secure-1PSIDTS rotated — will persist to config.")

                if changed:
                    write_config(CONFIG)
                    logger.info("Rotated Gemini cookies persisted to config.conf.")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Cookie persist check failed: {e}")

        await asyncio.sleep(600)  # Re-check every 10 minutes


def start_cookie_persister() -> asyncio.Task:
    """Start the background cookie-persist task. Safe to call multiple times."""
    global _persist_task
    if _persist_task is not None and not _persist_task.done():
        return _persist_task
    _persist_task = asyncio.create_task(_persist_cookies_loop())
    logger.info("Cookie persist task started (checks every 10 min).")
    return _persist_task


def stop_cookie_persister():
    """Cancel the cookie persister task on shutdown."""
    global _persist_task
    if _persist_task is not None and not _persist_task.done():
        _persist_task.cancel()
        logger.info("Cookie persist task stopped.")
    _persist_task = None

