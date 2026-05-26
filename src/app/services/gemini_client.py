# src/app/services/gemini_client.py
import os
import tempfile
from models.gemini import MyGeminiClient
from app.config import CONFIG
from app.logger import logger
from app.utils.browser import get_cookie_from_browser

# Import the specific exception to handle it gracefully
from gemini_webapi.exceptions import AuthError


class GeminiClientNotInitializedError(Exception):
    """Raised when the Gemini client is not initialized or initialization failed."""
    pass


# Global variables to store the Gemini client instance and state
_gemini_client = None
_initialization_error = None
_client_pid = None

async def init_gemini_client() -> bool:
    """
    Initialize and set up the Gemini client based on the configuration.
    Returns True on success, False on failure.
    """
    global _gemini_client, _initialization_error, _client_pid
    _initialization_error = None

    if CONFIG.getboolean("EnabledAI", "gemini", fallback=True):
        try:
            # 1. Try with cookies from config first
            config_cookies = dict(CONFIG["Cookies"]) if "Cookies" in CONFIG else {}
            gemini_proxy = CONFIG["Proxy"].get("http_proxy")

            if gemini_proxy == "":
                gemini_proxy = None

            client = None
            
            if config_cookies:
                # Strip potential double quotes from cookie values added manually by the user
                cleaned_cookies = {k: v.strip('"') for k, v in config_cookies.items()}
                
                logger.info(f"Attempting to initialize Gemini client with {len(cleaned_cookies)} cookies from config...")
                
                # IMPORTANT: Disable the library's internal file-based caching to avoid "downgrading" 
                # our session from 24 cookies to 7-8 cookies.
                os.environ["GEMINI_COOKIE_PATH"] = os.path.join(tempfile.gettempdir(), "webai_no_cache_" + str(os.getpid()))
                
                # Extract primary cookies for constructor
                psid = cleaned_cookies.get("__Secure-1PSID") or cleaned_cookies.get("gemini_cookie_1PSID") or cleaned_cookies.get("gemini_cookie_1psid")
                psidts = cleaned_cookies.get("__Secure-1PSIDTS") or cleaned_cookies.get("gemini_cookie_1PSIDTS") or cleaned_cookies.get("gemini_cookie_1psidts")
                
                client = MyGeminiClient(secure_1psid=psid, secure_1psidts=psidts, proxy=gemini_proxy, cookies=cleaned_cookies)
                
                # We disable auto_refresh to maintain full control over the session and cookies
                await client.init(verbose=True, auto_refresh=False)
                
                # Check if authenticated
                if hasattr(client.client, 'account_status') and client.client.account_status.name != "AVAILABLE":
                    logger.warning(f"Config cookies are unauthenticated (Status: {client.client.account_status.name}). Falling back to browser cookies...")
                    await client.close()
                    client = None
                else:
                    logger.info("Gemini client initialized successfully with config cookies.")

            # 2. Fallback to browser cookies if config cookies failed or were missing
            if client is None:
                logger.info("Attempting to fetch fresh cookies from browser...")
                browser_cookies = get_cookie_from_browser("gemini")
                if browser_cookies:
                    logger.info("Retrieved cookies from browser. Initializing client...")
                    # We pass the full jar to MyGeminiClient
                    client = MyGeminiClient(proxy=gemini_proxy, cookies=browser_cookies)
                    await client.init(verbose=True, auto_refresh=False)
                    
                    if hasattr(client.client, 'account_status') and client.client.account_status.name != "AVAILABLE":
                        logger.error(f"Browser cookies are also unauthenticated (Status: {client.client.account_status.name}).")
                        # We still keep it, but it might have limited functionality (Guest Mode)
                    else:
                        logger.info("Gemini client initialized successfully with browser cookies.")
                else:
                    error_msg = "Gemini cookies not found in config or browser. Please ensure browser is logged in."
                    logger.error(error_msg)
                    _initialization_error = error_msg
                    return False

            _gemini_client = client
            _client_pid = os.getpid()
            return True

        except AuthError as e:
            error_msg = f"Gemini authentication failed: {e}. This usually means cookies are expired or invalid."
            logger.error(error_msg)
            _gemini_client = None
            _client_pid = None
            _initialization_error = error_msg
            return False

        except Exception as e:
            error_msg = f"Unexpected error initializing Gemini client: {e}"
            logger.error(error_msg, exc_info=True)
            _gemini_client = None
            _client_pid = None
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
        GeminiClientNotInitializedError: If the client is not initialized or from a different process.
    """
    if _gemini_client is None:
        error_detail = _initialization_error or "Gemini client was not initialized. Check logs for details."
        raise GeminiClientNotInitializedError(error_detail)
    
    # Check if the client was initialized in the current process
    if _client_pid != os.getpid():
        raise GeminiClientNotInitializedError("Gemini client belongs to a different process (forked). Reinitialization required.")
        
    return _gemini_client

