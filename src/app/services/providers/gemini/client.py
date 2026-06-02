# src/app/services/providers/gemini/client.py
import os
import tempfile
import asyncio
import inspect
from .webapi_client import MyGeminiClient
from app.services.browser.auth_loader import GeminiAuthStateLoader
from app.config import CONFIG, get_default_auth_state_dir
from app.logger import logger
from app.utils.browser import get_cookie_from_browser
from app.services.providers.gemini.auth_selector import GeminiAuthSelector

# Import the specific exception to handle it gracefully
from gemini_webapi.exceptions import AuthError


class GeminiClientNotInitializedError(Exception):
    """Raised when the Gemini client is not initialized or initialization failed."""
    pass


# Global variables to store the Gemini client instance and state
_gemini_client = None
_initialization_error = None
_gemini_client_auth_source = None
_gemini_client_init_lock = asyncio.Lock()


def get_gemini_client_auth_source():
    """
    Return the currently selected WebAPI auth source label, if known.
    """
    return _gemini_client_auth_source


async def init_gemini_client() -> bool:
    """
    Initialize and set up the Gemini client based on the configuration and canonical storage.
    Returns True on success, False on failure.
    """
    global _gemini_client, _initialization_error, _gemini_client_auth_source
    
    async with _gemini_client_init_lock:
        _initialization_error = None

        if _gemini_client is not None:
            logger.info("Closing existing Gemini client before re-initialization...")
            try:
                if hasattr(_gemini_client, "close"):
                    res = _gemini_client.close()
                    if inspect.isawaitable(res):
                        await res
            except Exception as e:
                logger.warning(f"Error closing existing Gemini client: {e}")
            _gemini_client = None

        if not CONFIG.getboolean("EnabledAI", "gemini", fallback=True):
            error_msg = "Gemini client is disabled in config."
            logger.info(error_msg)
            _initialization_error = error_msg
            return False

        gemini_proxy = CONFIG["Proxy"].get("http_proxy")
        if gemini_proxy == "":
            gemini_proxy = None

        import time
        # Disable library's internal file-based caching with a unique session identifier to prevent pollution/collisions
        unique_session_id = f"{os.getpid()}_{int(time.time())}"
        os.environ["GEMINI_COOKIE_PATH"] = os.path.join(tempfile.gettempdir(), f"webai_no_cache_{unique_session_id}")

        best_client = None
        best_client_source_name = None
        client = None

        try:
            # Step 1: Try config/store candidates in selector-defined priority order
            for candidate in GeminiAuthSelector.iter_candidates():
                if candidate.supports_webapi_cookie_auth:
                    cookies_dict, psid, psidts = GeminiAuthStateLoader.translate_to_webapi(candidate.auth_data)
                    if psid:
                        logger.info(f"Attempting to initialize Gemini client with cookies from {candidate.source_name}...")
                        try:
                            client = MyGeminiClient(secure_1psid=psid, secure_1psidts=psidts, proxy=gemini_proxy, cookies=cookies_dict)
                            await client.init(verbose=True, auto_refresh=False)

                            status_name = client.client.account_status.name if hasattr(client.client, 'account_status') else "UNKNOWN"
                            if status_name == "AVAILABLE":
                                logger.info(f"Gemini client successfully initialized as authenticated client using {candidate.source_name}.")
                                if best_client:
                                    await best_client.close()
                                    best_client = None
                                    best_client_source_name = None
                                _gemini_client = client
                                _gemini_client_auth_source = candidate.source_name
                                return True
                            elif status_name == "UNAUTHENTICATED":
                                if best_client is None:
                                    logger.info(f"Cookies from {candidate.source_name} are unauthenticated. Holding as fallback, continuing to next source...")
                                    best_client = client
                                    best_client_source_name = candidate.source_name
                                    client = None
                                else:
                                    logger.info(f"Cookies from {candidate.source_name} are unauthenticated. Already have a fallback candidate, closing client.")
                                    await client.close()
                                    client = None
                            else:
                                logger.warning(f"Cookies from {candidate.source_name} are blocked or invalid (Status: {status_name}). Closing client.")
                                await client.close()
                                client = None
                        except Exception as e:
                            logger.warning(f"Gemini client initialization failed with cookies from {candidate.source_name}: {e}. Continuing to next source...")
                            if client:
                                await client.close()
                                client = None

            # Step 2: Try browser cookies fallback
            if _gemini_client is None:
                try:
                    logger.info("Attempting to fetch fresh cookies from browser...")
                    browser_cookies = get_cookie_from_browser("gemini")
                    if browser_cookies:
                        logger.info("Retrieved cookies from browser. Initializing client...")
                        psid = browser_cookies.get("__Secure-1PSID")
                        psidts = browser_cookies.get("__Secure-1PSIDTS")
                        client = MyGeminiClient(secure_1psid=psid, secure_1psidts=psidts, proxy=gemini_proxy, cookies=browser_cookies)
                        await client.init(verbose=True, auto_refresh=False)
                        
                        status_name = client.client.account_status.name if hasattr(client.client, 'account_status') else "UNKNOWN"
                        if status_name == "AVAILABLE":
                            logger.info("Gemini client successfully initialized as authenticated client with browser cookies.")
                            if best_client:
                                await best_client.close()
                                best_client = None
                                best_client_source_name = None
                            _gemini_client = client
                            _gemini_client_auth_source = "browser cookie fallback"
                            return True
                        elif status_name == "UNAUTHENTICATED":
                            if best_client is None:
                                logger.info("Browser cookies are unauthenticated. Holding browser client as fallback candidate.")
                                best_client = client
                                best_client_source_name = "browser cookie fallback"
                                client = None
                            else:
                                logger.info("Browser cookies are unauthenticated. Already have a fallback candidate, closing browser client.")
                                await client.close()
                                client = None
                        else:
                            logger.warning(f"Browser cookies are blocked or invalid (Status: {status_name}). Closing browser client.")
                            await client.close()
                            client = None
                except Exception as e:
                    logger.warning(f"Browser cookie initialization failed: {e}.")
                    if client:
                        await client.close()
                        client = None

            # Step 3: Final Candidate Resolution
            if _gemini_client is None and best_client is not None:
                logger.info("No fully authenticated AVAILABLE session found. Retaining the guest-mode client fallback candidate.")
                _gemini_client = best_client
                _gemini_client_auth_source = best_client_source_name
                return True

            # If we got here, all attempts failed
            error_msg = "Gemini cookies not found or completely invalid in canonical store, legacy config, or browser."
            logger.error(error_msg)
            _initialization_error = error_msg
            _gemini_client_auth_source = None
            return False

        except Exception as e:
            error_msg = f"Unexpected error initializing Gemini client waterfall: {e}"
            logger.error(error_msg, exc_info=True)
            _initialization_error = error_msg
            _gemini_client_auth_source = None
            
            # Clean up any leftover active clients in case of a waterfall exception
            if client:
                try:
                    await client.close()
                except Exception:
                    pass
            if best_client:
                try:
                    await best_client.close()
                except Exception:
                    pass
            
            _gemini_client = None
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
