# src/app/services/browser/auth_manager.py
import os
import asyncio
import time
import threading
from typing import Dict, Any, Optional
from app.config import CONFIG
from app.logger import logger
from app.services.browser.auth_types import AuthStatus, LoginState

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

        self._strategy = None
        self._cached_playwright_status = None
        self._cached_webapi_status = None
        self._cached_webapi_source = None
        self._last_validated = 0.0
        self._active_login_task: Optional[asyncio.Task] = None
        self._legacy_fallback_active = False
        self._initialized = True

    def set_strategy(self, strategy: Any) -> None:
        """
        Register a provider-specific authentication strategy.
        """
        self._strategy = strategy

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

    def refresh_playwright_status_lightweight(self) -> str:
        """
        Lightweight check for Playwright session status.
        Delegates to the registered strategy.
        """
        status = self.refresh_status()
        return status.get("playwright_status", AuthStatus.NO_SESSION)

    def refresh_webapi_status_lightweight(self) -> str:
        """
        Lightweight check for gemini-webapi connection status.
        Delegates to the registered strategy.
        """
        status = self.refresh_status()
        return status.get("webapi_status", AuthStatus.INVALID)

    def refresh_status(self) -> Dict[str, Any]:
        """
        Perform a lightweight refresh of both authentication pathways.
        Delegates to the registered strategy.
        """
        if not self._strategy:
            return {
                "playwright_status": AuthStatus.NO_SESSION,
                "webapi_status": AuthStatus.INVALID,
                "last_validated": time.time()
            }

        res = self._strategy.refresh_status()
        self._cached_playwright_status = res.get("playwright")
        self._cached_webapi_status = res.get("webapi")
        self._cached_webapi_source = res.get("webapi_source")
        self._legacy_fallback_active = res.get("is_legacy", False)
        self._last_validated = time.time()
        
        return {
            "playwright_status": self._cached_playwright_status,
            "webapi_status": self._cached_webapi_status,
            "last_validated": self._last_validated
        }

    def get_status(self) -> Dict[str, Any]:
        """
        Get the current cached authentication status without performing expensive operations.
        """
        if self._cached_playwright_status is None or self._cached_webapi_status is None:
            self.refresh_status()

        state_path = self._strategy.get_state_path() if self._strategy else "N/A"

        status_payload = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "login_state": self.login_state,
            "gemini_webapi": {
                "status": self._cached_webapi_status,
                "auth_source": self._cached_webapi_source
            },
            "playwright": {
                "status": self._cached_playwright_status,
                "auth_state_file": state_path,
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
        """
        if not self._strategy:
            raise RuntimeError("No authentication strategy registered.")
            
        if not self.coordination_lock.acquire():
            raise ValueError("Authentication in progress.")

        self._active_login_task = asyncio.create_task(self._run_login_task())
        logger.info("AuthManager: Asynchronous login workflow successfully triggered.")

    async def _run_login_task(self) -> None:
        async with self.login_lock:
            try:
                if not self._check_display_available():
                    raise RuntimeError("Headful interactive sign-in is unsupported in this headless container environment.")

                from app.services.browser.engine import get_browser_engine
                bootstrap_engine = await get_browser_engine(headless=False, is_bootstrap=True)
                
                async with bootstrap_engine as engine:
                    await self._strategy.run_login_flow(engine)
                
                self._cached_playwright_status = AuthStatus.VALID_SESSION
                
                # Re-initialize the direct gemini-webapi client with the newly saved cookies
                try:
                    await self._strategy.run_post_login_recovery()
                    logger.info("AuthManager: Instantly refreshing local authentication statuses...")
                    self.refresh_status()
                except Exception as e:
                    logger.error(f"AuthManager: Post-login recovery failed: {e}")
                    self._cached_webapi_status = AuthStatus.INVALID
            except Exception as e:
                logger.error(f"AuthManager: Background login flow failed: {e}")
                # Refresh status (which might be EXPIRED_SESSION or NO_SESSION)
                self.refresh_status()
            finally:
                self._active_login_task = None
                self._last_validated = time.time()
                self.coordination_lock.release()

    async def run_login_flow(self) -> None:
        """
        Orchestrate the headful login workflow using core BrowserEngine primitives.
        Delegates to the registered strategy.
        """
        if not self._strategy:
            raise RuntimeError("No authentication strategy registered.")
            
        if not self._check_display_available():
            raise RuntimeError("Headful interactive sign-in is unsupported in this headless container environment.")

        from app.services.browser.engine import get_browser_engine
        bootstrap_engine = await get_browser_engine(headless=False, is_bootstrap=True)
        async with bootstrap_engine as engine:
            await self._strategy.run_login_flow(engine)

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
