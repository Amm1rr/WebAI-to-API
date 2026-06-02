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
        self._default_provider_name = "gemini"
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

        self._strategies: Dict[str, Any] = {}
        self._strategy = None
        self._cached_playwright_status = None
        self._cached_webapi_status = None
        self._cached_webapi_source = None
        self._last_validated = 0.0
        self._cached_status_by_provider: Dict[str, Dict[str, Any]] = {}
        self._active_login_task: Optional[asyncio.Task] = None
        self._active_login_tasks_by_provider: Dict[str, asyncio.Task] = {}
        self._legacy_fallback_active = False
        self._initialized = True

    def _resolve_provider_name(self, strategy: Any, provider_name: Optional[str] = None) -> str:
        if provider_name:
            return provider_name

        candidate = getattr(strategy, "provider_name", None)
        if callable(candidate):
            candidate = candidate()
        if isinstance(candidate, str) and candidate:
            return candidate

        return self._default_provider_name

    def register_strategy(self, provider_name: str, strategy: Any) -> None:
        """
        Register a provider-specific authentication strategy.
        """
        if not provider_name:
            raise ValueError("provider_name is required.")

        self._strategies[provider_name] = strategy
        if provider_name == self._default_provider_name:
            self._strategy = strategy

    def set_strategy(self, strategy: Any) -> None:
        """
        Backward-compatible alias for registering the default Gemini strategy.
        """
        provider_name = self._resolve_provider_name(strategy)
        self.register_strategy(provider_name, strategy)

    def get_strategy(self, provider_name: Optional[str] = None) -> Any:
        provider_name = provider_name or self._default_provider_name
        if provider_name == self._default_provider_name and self._strategy is not None:
            return self._strategy
        return self._strategies.get(provider_name)

    def _empty_provider_status(self) -> Dict[str, Any]:
        return {
            "playwright": AuthStatus.NO_SESSION,
            "webapi": AuthStatus.INVALID,
            "webapi_source": None,
            "is_legacy": False,
            "last_validated": 0.0,
        }

    def _sync_default_legacy_cache(self, snapshot: Dict[str, Any]) -> None:
        self._cached_playwright_status = snapshot.get("playwright")
        self._cached_webapi_status = snapshot.get("webapi")
        self._cached_webapi_source = snapshot.get("webapi_source")
        self._legacy_fallback_active = snapshot.get("is_legacy", False)
        self._last_validated = time.time()

    def _store_provider_snapshot(self, provider_name: str, snapshot: Dict[str, Any]) -> None:
        self._cached_status_by_provider[provider_name] = dict(snapshot)
        if provider_name == self._default_provider_name:
            self._sync_default_legacy_cache(snapshot)

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

    def refresh_playwright_status_lightweight(self, provider_name: Optional[str] = None) -> str:
        """
        Lightweight check for Playwright session status.
        Delegates to the registered strategy.
        """
        status = self.refresh_status(provider_name)
        return status.get("playwright_status", AuthStatus.NO_SESSION)

    def refresh_webapi_status_lightweight(self, provider_name: Optional[str] = None) -> str:
        """
        Lightweight check for gemini-webapi connection status.
        Delegates to the registered strategy.
        """
        status = self.refresh_status(provider_name)
        return status.get("webapi_status", AuthStatus.INVALID)

    def refresh_status(self, provider_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Perform a lightweight refresh of both authentication pathways.
        Delegates to the registered strategy.
        """
        provider_name = provider_name or self._default_provider_name
        strategy = self.get_strategy(provider_name)
        if not strategy:
            snapshot = self._empty_provider_status()
            snapshot["last_validated"] = time.time()
            self._store_provider_snapshot(provider_name, snapshot)
            return {
                "playwright_status": snapshot["playwright"],
                "webapi_status": snapshot["webapi"],
                "last_validated": snapshot["last_validated"],
            }

        res = strategy.refresh_status()
        snapshot = {
            "playwright": res.get("playwright", AuthStatus.NO_SESSION),
            "webapi": res.get("webapi", AuthStatus.INVALID),
            "webapi_source": res.get("webapi_source"),
            "is_legacy": res.get("is_legacy", False),
            "last_validated": time.time(),
        }
        self._store_provider_snapshot(provider_name, snapshot)
        
        return {
            "playwright_status": snapshot["playwright"],
            "webapi_status": snapshot["webapi"],
            "last_validated": snapshot["last_validated"]
        }

    def get_status(self) -> Dict[str, Any]:
        """
        Get the current cached authentication status without performing expensive operations.
        """
        if self._cached_playwright_status is None or self._cached_webapi_status is None:
            self.refresh_status(self._default_provider_name)

        default_strategy = self.get_strategy(self._default_provider_name)
        state_path = default_strategy.get_state_path() if default_strategy else "N/A"

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

    def get_provider_status(self, provider_name: Optional[str] = None) -> Dict[str, Any]:
        provider_name = provider_name or self._default_provider_name
        snapshot = self._cached_status_by_provider.get(provider_name)
        if snapshot is None:
            self.refresh_status(provider_name)
            snapshot = self._cached_status_by_provider.get(provider_name, self._empty_provider_status())
        return dict(snapshot)

    def mark_expired(self, provider_name: Optional[str] = None):
        """
        Transition cached status to EXPIRED_SESSION. Called passively on request auth failures.
        """
        provider_name = provider_name or self._default_provider_name
        logger.info("AuthManager: Playwright session marked as EXPIRED_SESSION passively.")
        snapshot = self._cached_status_by_provider.get(provider_name, self._empty_provider_status())
        snapshot["playwright"] = AuthStatus.EXPIRED_SESSION
        self._store_provider_snapshot(provider_name, snapshot)

    def start_login(self, provider_name: Optional[str] = None) -> None:
        """
        Triggers the on-demand bootstrap login workflow asynchronously in the background.
        Coordinates locks and active state machine.
        """
        provider_name = provider_name or self._default_provider_name
        strategy = self.get_strategy(provider_name)
        if not strategy:
            raise RuntimeError("No authentication strategy registered.")
            
        if not self.coordination_lock.acquire():
            raise ValueError("Authentication in progress.")

        task = asyncio.create_task(self._run_login_task(provider_name))
        self._active_login_tasks_by_provider[provider_name] = task
        if provider_name == self._default_provider_name:
            self._active_login_task = task
        logger.info("AuthManager: Asynchronous login workflow successfully triggered.")

    async def _run_login_task(self, provider_name: str) -> None:
        async with self.login_lock:
            try:
                if not self._check_display_available():
                    raise RuntimeError("Headful interactive sign-in is unsupported in this headless container environment.")

                from app.services.browser.engine import get_browser_engine
                bootstrap_engine = await get_browser_engine(headless=False, is_bootstrap=True)
                strategy = self.get_strategy(provider_name)
                if not strategy:
                    raise RuntimeError("No authentication strategy registered.")
                
                async with bootstrap_engine as engine:
                    await strategy.run_login_flow(engine)
                
                snapshot = self._cached_status_by_provider.get(provider_name, self._empty_provider_status())
                snapshot["playwright"] = AuthStatus.VALID_SESSION
                self._store_provider_snapshot(provider_name, snapshot)
                
                # Re-initialize the direct gemini-webapi client with the newly saved cookies
                try:
                    await strategy.run_post_login_recovery()
                    logger.info("AuthManager: Instantly refreshing local authentication statuses...")
                    self.refresh_status(provider_name)
                except Exception as e:
                    logger.error(f"AuthManager: Post-login recovery failed: {e}")
                    snapshot = self._cached_status_by_provider.get(provider_name, self._empty_provider_status())
                    snapshot["webapi"] = AuthStatus.INVALID
                    self._store_provider_snapshot(provider_name, snapshot)
            except Exception as e:
                logger.error(f"AuthManager: Background login flow failed: {e}")
                # Refresh status (which might be EXPIRED_SESSION or NO_SESSION)
                self.refresh_status(provider_name)
            finally:
                self._active_login_tasks_by_provider.pop(provider_name, None)
                if provider_name == self._default_provider_name:
                    self._active_login_task = None
                snapshot = self._cached_status_by_provider.get(provider_name, self._empty_provider_status())
                snapshot["last_validated"] = time.time()
                self._store_provider_snapshot(provider_name, snapshot)
                self.coordination_lock.release()

    async def run_login_flow(self, provider_name: Optional[str] = None) -> None:
        """
        Orchestrate the headful login workflow using core BrowserEngine primitives.
        Delegates to the registered strategy.
        """
        provider_name = provider_name or self._default_provider_name
        strategy = self.get_strategy(provider_name)
        if not strategy:
            raise RuntimeError("No authentication strategy registered.")
            
        if not self._check_display_available():
            raise RuntimeError("Headful interactive sign-in is unsupported in this headless container environment.")

        from app.services.browser.engine import get_browser_engine
        bootstrap_engine = await get_browser_engine(headless=False, is_bootstrap=True)
        async with bootstrap_engine as engine:
            await strategy.run_login_flow(engine)

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
