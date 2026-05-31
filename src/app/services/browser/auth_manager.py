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

class AuthCoordinationLock:
    """
    Abstract base interface for authentication coordination locking.
    Defines the contract for multi-worker and distributed scaling.
    """
    def acquire(self) -> bool:
        raise NotImplementedError

    def release(self) -> None:
        raise NotImplementedError

    def is_locked(self) -> bool:
        raise NotImplementedError

class InMemoryAuthLock(AuthCoordinationLock):
    """
    Process-bound in-memory implementation of AuthCoordinationLock.
    Note: Thread-safe within a single process.
    Not multi-worker safe or distributed-safe.
    """
    def __init__(self):
        self._lock = threading.Lock()

    def acquire(self) -> bool:
        return self._lock.acquire(blocking=False)

    def release(self) -> None:
        try:
            self._lock.release()
        except RuntimeError:
            pass

    def is_locked(self) -> bool:
        return self._lock.locked()

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
        
        # Resolve auth lock backend from configuration
        backend_name = CONFIG["Playwright"].get("auth_lock_backend", "in_memory").strip().lower()
        if backend_name != "in_memory":
            logger.warning(
                f"AuthManager: Configured auth lock backend '{backend_name}' is not implemented. "
                "Falling back to default 'in_memory' backend. "
                "Production multi-worker or SaaS deployments must utilize a distributed lock backend (e.g. Redis SET NX or Postgres advisory locks)."
            )
            backend_name = "in_memory"

        # Check for multiple workers/processes under in-memory lock
        self._check_multi_worker_warning(backend_name)

        if backend_name == "in_memory":
            self.coordination_lock = InMemoryAuthLock()

        self._cached_playwright_status = None
        self._cached_webapi_status = None
        self._last_validated = 0.0
        self._active_login_task: Optional[asyncio.Task] = None
        self._legacy_fallback_active = False
        self._initialized = True

    @property
    def login_state(self) -> str:
        if hasattr(self, 'coordination_lock') and self.coordination_lock.is_locked():
            return LoginState.LOGIN_IN_PROGRESS
        return LoginState.IDLE

    def _check_multi_worker_warning(self, backend_name: str) -> None:
        if backend_name == "in_memory":
            workers = os.environ.get("WEB_CONCURRENCY") or os.environ.get("WORKERS")
            if workers:
                try:
                    if int(workers) > 1:
                        logger.warning(
                            "AuthManager: Multiple workers detected under 'in_memory' lock backend. "
                            "Concurrency protection is process-bound and not multi-worker or SaaS-safe. "
                            "For multi-worker deployments, a distributed lock backend (e.g., Redis or Postgres) must be configured."
                        )
                except ValueError:
                    pass

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

        NOTE: This only performs a structural and syntax check of the authentication state.
        It validates JSON integrity and checks for necessary cookie schemas.
        It does NOT execute any DOM-level or server-side active authentication checks,
        which are deferred to runtime request routing to maintain zero-latency status endpoints.
        """
        from app.services.browser.auth_loader import GeminiAuthStateLoader
        
        # Resolve authentication utilizing prioritized hierarchy in GeminiAuthStateLoader
        auth_data, is_legacy = GeminiAuthStateLoader.load_auth_state_with_fallback()
        
        if auth_data:
            self._cached_playwright_status = AuthStatus.VALID_SESSION
            self._legacy_fallback_active = is_legacy
            return AuthStatus.VALID_SESSION

        # Neither present
        self._cached_playwright_status = AuthStatus.NO_SESSION
        self._legacy_fallback_active = False
        return AuthStatus.NO_SESSION

    def refresh_webapi_status_lightweight(self) -> str:
        """
        Lightweight check for gemini-webapi connection status.
        """
        try:
            import app.services.gemini_client as gc
            client_instance = gc._gemini_client
            if client_instance is None:
                self._cached_webapi_status = AuthStatus.INVALID
                return AuthStatus.INVALID

            status_name = client_instance.client.account_status.name if hasattr(client_instance.client, 'account_status') else "UNKNOWN"
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

        status_payload = {
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

        # Expose legacy fallback and migration needed metrics if legacy cookies are active
        if getattr(self, "_legacy_fallback_active", False):
            status_payload["playwright"]["legacy_fallback_active"] = True
            status_payload["playwright"]["migration_needed"] = True

        return status_payload

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
        Checking and transitioning the login state are protected by the AuthCoordinationLock.
        This provides a clear abstraction boundary so that future multi-worker or SaaS
        deployments can swap the default InMemoryAuthLock for a distributed lock backend
        (such as Redis SET NX or Postgres advisory locks).
        """
        if not self.coordination_lock.acquire():
            raise ValueError("Authentication in progress.")

        self._active_login_task = asyncio.create_task(self._run_login_task())
        logger.info("AuthManager: Asynchronous login workflow successfully triggered.")

    async def _run_login_task(self) -> None:
        async with self.login_lock:
            try:
                await self.run_login_flow()
                self._cached_playwright_status = AuthStatus.VALID_SESSION
                
                # Re-initialize the direct gemini-webapi client with the newly saved cookies
                try:
                    from app.services.gemini_client import init_gemini_client
                    from app.services.session_manager import init_session_managers
                    from app.services.factory import ProviderFactory
                    
                    logger.info("AuthManager: Clearing and closing registered Gemini provider in ProviderFactory...")
                    await ProviderFactory.close_provider("gemini")

                    logger.info("AuthManager: Re-initializing direct Gemini WebAPI client...")
                    init_success = await init_gemini_client()
                    if not init_success:
                        raise RuntimeError("Gemini direct client initialization returned False.")
                    
                    logger.info("AuthManager: Re-initializing session managers with new client...")
                    await init_session_managers()
                    
                    logger.info("AuthManager: Instantly refreshing local authentication statuses...")
                    self.refresh_status()
                except Exception as e:
                    logger.error(f"AuthManager: Direct Gemini WebAPI client re-initialization failed: {e}")
                    self._cached_webapi_status = AuthStatus.INVALID
                    self.refresh_playwright_status_lightweight()
            except Exception as e:
                logger.error(f"AuthManager: Background login flow failed: {e}")
                # Refresh playwright status (which might be EXPIRED_SESSION or NO_SESSION)
                self.refresh_playwright_status_lightweight()
            finally:
                self._active_login_task = None
                self._last_validated = time.time()
                self.coordination_lock.release()

    async def run_login_flow(self) -> None:
        """
        Orchestrate the headful login workflow using core BrowserEngine primitives.
        """
        from app.services.browser.engine import get_browser_engine
        from app.services.browser.adapters.scripts.gemini_scripts import SELECTORS
        
        # 1. Perform environment checks
        if not self._check_display_available():
            raise RuntimeError("Headful interactive sign-in is unsupported in this headless container environment.")

        # Robust selectors for verification to prevent false-positives
        SIGN_IN_SELECTORS = [
            'a[href*="accounts.google.com"]',
            'a:has-text("Sign in")',
            'button:has-text("Sign in")',
            'a[aria-label*="Sign in"]',
            '.sign-in-button'
        ]

        AUTHENTICATED_SELECTORS = [
            'a[href*="SignOutOptions"]',
            'a[href*="myaccount.google.com"]',
            'img[src*="googleusercontent.com"]',
            '[aria-label*="Google Account"]'
        ]

        bootstrap_engine = await get_browser_engine(headless=False, is_bootstrap=True)
        async with bootstrap_engine as engine:
            logger.info("AuthManager: Launching isolated bootstrap browser...")
            page_wrapper = await engine.get_page("gemini", enable_persistence=True)
            page = page_wrapper.page
            session = await engine.get_session("gemini", enable_persistence=True)
            
            logger.info("AuthManager: Navigating to https://gemini.google.com/app...")
            await page.goto("https://gemini.google.com/app")
            
            login_detected = False
            user_closed = False
            last_state = None
            try:
                # Poll every 2 seconds for a maximum of 5 minutes (150 iterations)
                for _ in range(150):
                    if page.is_closed():
                        user_closed = True
                        break
                    
                    # 1. Check if the page is currently on a Google login/account URL
                    current_url = page.url
                    is_google_login = "accounts.google.com" in current_url
                    
                    # 2. Check for visible sign-in buttons on the page
                    has_sign_in_button = False
                    for selector in SIGN_IN_SELECTORS:
                        try:
                            if await page.locator(selector).first.is_visible():
                                has_sign_in_button = True
                                break
                        except Exception:
                            pass
                    
                    # 3. Check if the Gemini chat input box is visible
                    input_visible = await page.locator(SELECTORS["INPUT"]).first.is_visible()
                    
                    # 4. Check for Google Account/profile avatar or menu indicators
                    has_auth_indicator = False
                    for selector in AUTHENTICATED_SELECTORS:
                        try:
                            if await page.locator(selector).first.is_visible():
                                has_auth_indicator = True
                                break
                        except Exception:
                            pass

                    # 5. Determine active state
                    if is_google_login:
                        current_state = "sign_in_page_detected"
                    elif input_visible and not has_sign_in_button and "gemini.google.com" in current_url:
                        current_state = "authenticated_chat_detected"
                    else:
                        current_state = "waiting_for_user_login"

                    # Log transitions between states
                    if current_state != last_state:
                        if current_state == "sign_in_page_detected":
                            logger.info("AuthManager Status: sign_in_page_detected")
                        elif current_state == "authenticated_chat_detected":
                            logger.info("AuthManager Status: authenticated_chat_detected")
                        elif current_state == "waiting_for_user_login":
                            logger.info("AuthManager Status: waiting_for_user_login")
                        last_state = current_state

                    # If authentication is confirmed with strong evidence, save state and exit
                    if current_state == "authenticated_chat_detected":
                        login_detected = True
                        await session.save_state()
                        logger.info("AuthManager: Success! Google sign-in detected and state saved atomically.")
                        break

                    await asyncio.sleep(2)
            except Exception as e:
                logger.warning(f"AuthManager: Exception during login monitoring: {e}")
                try:
                    # If page is physically closed, or exception indicates target closure, classify as user_closed
                    err_msg = str(e).lower()
                    if (
                        "target closed" in err_msg
                        or "page closed" in err_msg
                        or "context closed" in err_msg
                        or page.is_closed()
                    ):
                        user_closed = True
                except Exception:
                    # Best-effort fallback: if page reference is completely broken/destroyed, assume closed by user
                    user_closed = True
            finally:
                if page_wrapper:
                    try:
                        await page_wrapper.close()
                    except Exception as e:
                        logger.debug(f"AuthManager: page wrapper cleanup ignored: {e}")
            
            if not login_detected:
                if user_closed:
                    logger.warning("AuthManager Status: user_closed_login_window")
                    raise RuntimeError("Interactive sign-in was closed by user.")
                else:
                    logger.warning("AuthManager Status: login_timeout")
                    raise TimeoutError("Interactive sign-in timed out.")

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
