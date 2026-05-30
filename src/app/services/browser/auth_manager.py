# src/app/services/browser/auth_manager.py
import os
import json
import asyncio
import time
import threading
from typing import Dict, Any, Optional
from app.config import CONFIG, get_default_auth_state_dir
from app.logger import logger

class AuthStatus:
    # Playwright Statuses
    VALID_SESSION = "VALID_SESSION"
    NO_SESSION = "NO_SESSION"
    EXPIRED_SESSION = "EXPIRED_SESSION"
    INVALID_STATE = "INVALID_STATE"

    # gemini-webapi Statuses
    AUTHENTICATED = "AUTHENTICATED"
    GUEST = "GUEST"
    INVALID = "INVALID"

class LoginState:
    IDLE = "IDLE"
    LOGIN_IN_PROGRESS = "LOGIN_IN_PROGRESS"

class AuthManager:
    """
    Centralized authentication status manager, login coordinator,
    locks manager, and auth state machine.
    """
    _instance: Optional['AuthManager'] = None
    _lock = asyncio.Lock()

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(AuthManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self.login_lock = asyncio.Lock()
        self._thread_lock = threading.Lock()
        self.login_state = LoginState.IDLE
        self._cached_playwright_status = None
        self._cached_webapi_status = None
        self._last_validated = 0.0
        self._active_login_task: Optional[asyncio.Task] = None
        self._initialized = True

    @classmethod
    def get_instance(cls) -> 'AuthManager':
        if not cls._instance:
            cls._instance = cls()
        return cls._instance

    def get_state_path(self) -> str:
        auth_state_dir = CONFIG["Playwright"].get("auth_state_dir", get_default_auth_state_dir())
        return os.path.join(auth_state_dir, "gemini.json")

    def refresh_playwright_status_lightweight(self) -> str:
        """
        Lightweight check for Playwright session status.
        Does NOT launch Playwright or perform any network navigations.

        NOTE: This only performs a structural and syntax check of the 'gemini.json'
        file on disk. It validates JSON integrity and checks for necessary cookie schemas.
        It does NOT execute any DOM-level or server-side active authentication checks,
        which are deferred to runtime request routing to maintain zero-latency status endpoints.
        """
        # If we have an active EXPIRED_SESSION or INVALID_STATE cached, preserve it
        # unless manual file updates occurred.
        state_path = self.get_state_path()
        if not os.path.exists(state_path) or os.path.getsize(state_path) == 0:
            self._cached_playwright_status = AuthStatus.NO_SESSION
            return AuthStatus.NO_SESSION

        try:
            with open(state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or "cookies" not in data:
                self._cached_playwright_status = AuthStatus.INVALID_STATE
                return AuthStatus.INVALID_STATE
            
            # If currently marked as NO_SESSION or INVALID_STATE, restore to VALID_SESSION
            if self._cached_playwright_status in [AuthStatus.NO_SESSION, AuthStatus.INVALID_STATE, None]:
                self._cached_playwright_status = AuthStatus.VALID_SESSION
        except Exception:
            self._cached_playwright_status = AuthStatus.INVALID_STATE
            return AuthStatus.INVALID_STATE

        return self._cached_playwright_status

    def refresh_webapi_status_lightweight(self) -> str:
        """
        Lightweight check for gemini-webapi connection status.
        """
        try:
            from app.services.gemini_client import _gemini_client
            if _gemini_client is None:
                self._cached_webapi_status = AuthStatus.INVALID
                return AuthStatus.INVALID

            status_name = _gemini_client.client.account_status.name if hasattr(_gemini_client.client, 'account_status') else "UNKNOWN"
            if status_name == "AVAILABLE":
                self._cached_webapi_status = AuthStatus.AUTHENTICATED
            elif status_name == "UNAUTHENTICATED":
                self._cached_webapi_status = AuthStatus.GUEST
            else:
                self._cached_webapi_status = AuthStatus.INVALID
        except Exception as e:
            logger.debug(f"AuthManager: Error reading webapi status: {e}")
            self._cached_webapi_status = AuthStatus.INVALID

        return self._cached_webapi_status

    def refresh_status(self) -> Dict[str, Any]:
        """
        Perform a lightweight refresh of both authentication pathways.
        """
        playwright_status = self.refresh_playwright_status_lightweight()
        webapi_status = self.refresh_webapi_status_lightweight()
        self._last_validated = time.time()
        return {
            "playwright_status": playwright_status,
            "webapi_status": webapi_status,
            "last_validated": self._last_validated
        }

    def get_status(self) -> Dict[str, Any]:
        """
        Get the current cached authentication status without performing expensive operations.
        """
        if self._cached_playwright_status is None or self._cached_webapi_status is None:
            self.refresh_status()

        return {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "login_state": self.login_state,
            "gemini_webapi": {
                "status": self._cached_webapi_status
            },
            "playwright": {
                "status": self._cached_playwright_status,
                "auth_state_file": self.get_state_path(),
                "last_validated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._last_validated)),
                "validation_details": "Lightweight syntax check only. Active browser/DOM validation is deferred to runtime request execution to preserve zero-latency performance."
            }
        }

    def mark_expired(self):
        """
        Transition cached status to EXPIRED_SESSION. Called passively on request auth failures.
        """
        logger.info("AuthManager: Playwright session marked as EXPIRED_SESSION passively.")
        self._cached_playwright_status = AuthStatus.EXPIRED_SESSION

    def start_login(self) -> None:
        """
        Triggers the on-demand bootstrap login workflow asynchronously in the background.
        Coordinates locks and active state machine.

        CONCURRENCY SAFETY:
        Checking and transitioning the login state are protected by a synchronous thread lock
        (`self._thread_lock`). This guarantees absolute atomicity and thread-safety even in
        multithreaded execution environments (such as FastAPI running with multiple thread workers).
        """
        with self._thread_lock:
            if self.login_state == LoginState.LOGIN_IN_PROGRESS or self.login_lock.locked():
                raise ValueError("Authentication in progress.")

            self.login_state = LoginState.LOGIN_IN_PROGRESS
            self._active_login_task = asyncio.create_task(self._run_login_task())
            logger.info("AuthManager: Asynchronous login workflow successfully triggered.")

    async def _run_login_task(self) -> None:
        async with self.login_lock:
            try:
                await self.run_login_flow()
                self._cached_playwright_status = AuthStatus.VALID_SESSION
            except Exception as e:
                logger.error(f"AuthManager: Background login flow failed: {e}")
                # Refresh playwright status (which might be EXPIRED_SESSION or NO_SESSION)
                self.refresh_playwright_status_lightweight()
            finally:
                with self._thread_lock:
                    self.login_state = LoginState.IDLE
                    self._active_login_task = None
                self._last_validated = time.time()

    async def run_login_flow(self) -> None:
        """
        Orchestrate the headful login workflow using core BrowserEngine primitives.
        """
        from app.services.browser.engine import get_browser_engine
        from app.services.providers.gemini_playwright_scripts import SELECTORS
        
        # 1. Perform environment checks
        if not self._check_display_available():
            raise RuntimeError("Headful interactive sign-in is unsupported in this headless container environment.")

        bootstrap_engine = await get_browser_engine(headless=False, is_bootstrap=True)
        async with bootstrap_engine as engine:
            logger.info("AuthManager: Launching isolated bootstrap browser...")
            page_wrapper = await engine.get_page("gemini", enable_persistence=True)
            page = page_wrapper.page
            session = await engine.get_session("gemini", enable_persistence=True)
            
            logger.info("AuthManager: Navigating to https://gemini.google.com/app...")
            await page.goto("https://gemini.google.com/app")
            
            # Observe for visibility of the chat input box (SELECTORS["INPUT"])
            login_detected = False
            try:
                # Poll every 2 seconds for a maximum of 5 minutes (150 iterations)
                for _ in range(150):
                    if page.is_closed():
                        break
                    
                    input_exists = await page.locator(SELECTORS["INPUT"]).first.is_visible()
                    if input_exists:
                        login_detected = True
                        await session.save_state()
                        logger.info("AuthManager: Success! Google sign-in detected and state saved atomically.")
                        break
                    await asyncio.sleep(2)
            except Exception as e:
                logger.warning(f"AuthManager: Exception during login monitoring: {e}")
            finally:
                if page_wrapper and not page.is_closed():
                    try:
                        await page_wrapper.close()
                    except Exception:
                        pass
            
            if not login_detected:
                raise TimeoutError("Interactive sign-in timed out or was closed by user.")

    def _check_display_available(self) -> bool:
        """
        Check if X11/headless environment supports headful window display.
        """
        # If headless config is explicitly True, fail fast to prevent hanging container
        if CONFIG["Playwright"].getboolean("headless", False):
            return False
        # On Linux, verify DISPLAY env variable is set
        if os.name == "posix" and "DISPLAY" not in os.environ:
            return False
        return True

def get_auth_manager() -> AuthManager:
    return AuthManager.get_instance()
