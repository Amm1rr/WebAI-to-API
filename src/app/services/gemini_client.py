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


def _load_playwright_cookies() -> tuple[dict | None, str | None, str | None]:
    """
    Load __Secure-1PSID and __Secure-1PSIDTS from auth_state/gemini.json if it exists.
    Returns (cookies_dict, secure_1psid, secure_1psidts) or (None, None, None).
    """
    import json
    auth_state_dir = CONFIG["Playwright"].get("auth_state_dir", "auth_state")
    state_path = os.path.join(auth_state_dir, "gemini.json")
    
    if not os.path.exists(state_path):
        return None, None, None
        
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            state_data = json.load(f)
            
        cookies_list = state_data.get("cookies", [])
        extracted_cookies = {}
        for cookie in cookies_list:
            if "google.com" in cookie.get("domain", ""):
                name = cookie.get("name")
                if name in ["__Secure-1PSID", "__Secure-1PSIDTS"]:
                    extracted_cookies[name] = cookie.get("value")
                    
        psid = extracted_cookies.get("__Secure-1PSID")
        psidts = extracted_cookies.get("__Secure-1PSIDTS")
        
        if psid:
            logger.info("Playwright auth state cookies found.")
            return extracted_cookies, psid, psidts
            
    except Exception as e:
        logger.error(f"Failed to parse Playwright auth state: {e}", exc_info=True)
        
    return None, None, None

async def init_gemini_client() -> bool:
    """
    Initialize and set up the Gemini client based on the configuration.
    Returns True on success, False on failure.
    """
    global _gemini_client, _initialization_error
    _initialization_error = None

    if not CONFIG.getboolean("EnabledAI", "gemini", fallback=True):
        error_msg = "Gemini client is disabled in config."
        logger.info(error_msg)
        _initialization_error = error_msg
        return False

    gemini_proxy = CONFIG["Proxy"].get("http_proxy")
    if gemini_proxy == "":
        gemini_proxy = None

    # Disable library's internal file-based caching
    os.environ["GEMINI_COOKIE_PATH"] = os.path.join(tempfile.gettempdir(), "webai_no_cache_" + str(os.getpid()))

    best_client = None
    client = None

    try:
        # Step 1: Try config cookies
        config_cookies = dict(CONFIG["Cookies"]) if "Cookies" in CONFIG else {}
        if config_cookies:
            try:
                cleaned_cookies = {k: v.strip('"') for k, v in config_cookies.items() if k in ["__Secure-1PSID", "__Secure-1PSIDTS"]}
                psid = cleaned_cookies.get("__Secure-1PSID") or cleaned_cookies.get("gemini_cookie_1PSID") or cleaned_cookies.get("gemini_cookie_1psid")
                psidts = cleaned_cookies.get("__Secure-1PSIDTS") or cleaned_cookies.get("gemini_cookie_1PSIDTS") or cleaned_cookies.get("gemini_cookie_1psidts")
                
                logger.info(f"Attempting to initialize Gemini client with {len(cleaned_cookies)} essential cookies from config...")
                
                client = MyGeminiClient(secure_1psid=psid, secure_1psidts=psidts, proxy=gemini_proxy, cookies=cleaned_cookies)
                await client.init(verbose=True, auto_refresh=False)
                
                status_name = client.client.account_status.name if hasattr(client.client, 'account_status') else "UNKNOWN"
                
                if status_name == "AVAILABLE":
                    logger.info("Gemini client successfully initialized as authenticated client with config cookies.")
                    _gemini_client = client
                    return True
                elif status_name == "UNAUTHENTICATED":
                    logger.info("Config cookies are unauthenticated. Holding config client as fallback candidate.")
                    best_client = client
                    client = None
                else:
                    logger.warning(f"Config cookies are blocked or invalid (Status: {status_name}). Closing config client.")
                    await client.close()
                    client = None
            except Exception as e:
                logger.warning(f"Config cookie initialization failed: {e}. Proceeding to fallbacks...")
                if client:
                    await client.close()
                    client = None

        # Step 2: Try Playwright auth state
        if _gemini_client is None:
            try:
                logger.info("Attempting to fetch cookies from Playwright auth state...")
                playwright_cookies, psid, psidts = _load_playwright_cookies()
                if playwright_cookies:
                    logger.info("Retrieved cookies from Playwright auth state. Initializing client...")
                    client = MyGeminiClient(secure_1psid=psid, secure_1psidts=psidts, proxy=gemini_proxy, cookies=playwright_cookies)
                    await client.init(verbose=True, auto_refresh=False)
                    
                    status_name = client.client.account_status.name if hasattr(client.client, 'account_status') else "UNKNOWN"
                    
                    if status_name == "AVAILABLE":
                        logger.info("Gemini client successfully initialized as authenticated client with Playwright cookies.")
                        if best_client:
                            await best_client.close()
                            best_client = None
                        _gemini_client = client
                        return True
                    elif status_name == "UNAUTHENTICATED":
                        if best_client is None:
                            logger.info("Playwright cookies are unauthenticated. Holding Playwright client as fallback candidate.")
                            best_client = client
                            client = None
                        else:
                            logger.info("Playwright cookies are unauthenticated. Already have a fallback candidate, closing Playwright client.")
                            await client.close()
                            client = None
                    else:
                        logger.warning(f"Playwright cookies are blocked or invalid (Status: {status_name}). Closing Playwright client.")
                        await client.close()
                        client = None
            except Exception as e:
                logger.warning(f"Playwright auth state initialization failed: {e}. Proceeding to fallbacks...")
                if client:
                    await client.close()
                    client = None

        # Step 3: Try browser cookies fallback
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
                        _gemini_client = client
                        return True
                    elif status_name == "UNAUTHENTICATED":
                        if best_client is None:
                            logger.info("Browser cookies are unauthenticated. Holding browser client as fallback candidate.")
                            best_client = client
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

        # Step 4: Final Candidate Resolution
        if _gemini_client is None and best_client is not None:
            logger.info("No fully authenticated AVAILABLE session found. Retaining the guest-mode client fallback candidate.")
            _gemini_client = best_client
            return True

        # If we got here, all attempts failed
        error_msg = "Gemini cookies not found or completely invalid in config, Playwright state, or browser."
        logger.error(error_msg)
        _initialization_error = error_msg
        return False

    except Exception as e:
        error_msg = f"Unexpected error initializing Gemini client waterfall: {e}"
        logger.error(error_msg, exc_info=True)
        _initialization_error = error_msg
        
        # Clean up any leftover active clients in case of a fatal waterfall exception
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

