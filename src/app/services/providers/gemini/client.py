# src/app/services/providers/gemini/client.py
import os
import tempfile
import asyncio
import inspect
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional
from .webapi_client import MyGeminiClient
from app.services.browser.auth_loader import GeminiAuthStateLoader
from app.config import CONFIG
from app.logger import logger
from app.utils.browser import get_cookie_from_browser
from app.services.providers.gemini.auth_selector import GeminiAuthSelector

# Import the specific exception to handle it gracefully
class GeminiClientNotInitializedError(Exception):
    """Raised when the Gemini client is not initialized or initialization failed."""
    pass


class GeminiGenerationUnavailableError(RuntimeError):
    """Raised when a requested Gemini generation is stale or unavailable."""
    pass


# Global variables to store the Gemini client instance and state
_gemini_client = None
_initialization_error = None
_gemini_client_auth_source = None
_gemini_client_init_lock = asyncio.Lock()
_gemini_generation_records = {}
_gemini_client_generations = {}
_current_gemini_generation = None
_gemini_shutdown_started = False


@dataclass
class GeminiGenerationRecord:
    generation: int
    client: object
    lease_count: int = 0
    retired: bool = False
    close_started: bool = False
    close_completed: bool = False


class GeminiClientLease:
    def __init__(self, record: GeminiGenerationRecord):
        self.client = record.client
        self.generation = record.generation
        self._record = record
        self._released = False
        self._transferred = False

    def transfer(self) -> None:
        """Transfer release ownership to a returned streaming generator."""
        if self._released:
            raise RuntimeError("Cannot transfer a released Gemini client lease.")
        if self._transferred:
            raise RuntimeError("Gemini client lease ownership was already transferred.")
        self._transferred = True

    @property
    def is_active(self) -> bool:
        return not self._released and self._record.lease_count > 0

    @property
    def is_transferred(self) -> bool:
        return self._transferred

    def assert_active(self) -> None:
        if not self.is_active:
            raise RuntimeError("Gemini client lease is no longer active.")

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        record = self._record
        if record.lease_count == 0:
            return
        record.lease_count -= 1
        if record.retired and record.lease_count == 0:
            await asyncio.shield(_close_generation_record(record))

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        if self._transferred:
            return False
        await asyncio.shield(self.release())


def _get_current_generation_record_strict():
    if _gemini_client is None:
        return None
    if _current_gemini_generation is None:
        raise RuntimeError("Gemini lifecycle invariant violated: current generation is not set.")

    record = _gemini_generation_records.get(_current_gemini_generation)
    if record is None:
        raise RuntimeError("Gemini lifecycle invariant violated: current generation record is missing.")
    if record.client is not _gemini_client:
        raise RuntimeError("Gemini lifecycle invariant violated: generation record does not match current client.")
    if _gemini_client_generations.get(id(_gemini_client)) != _current_gemini_generation:
        raise RuntimeError("Gemini lifecycle invariant violated: reverse generation mapping does not match.")
    return record


def _register_generation(client, generation):
    existing_record = _gemini_generation_records.get(generation)
    if existing_record is not None:
        if existing_record.client is client:
            return existing_record
        raise RuntimeError("Gemini generation is already assigned to another client.")

    existing_generation = _gemini_client_generations.get(id(client))
    if existing_generation is not None:
        existing = _gemini_generation_records.get(existing_generation)
        if existing is not None and existing.client is client:
            return existing
        raise RuntimeError("Gemini client identity is already mapped to another generation.")

    record = GeminiGenerationRecord(generation, client)
    _gemini_generation_records[generation] = record
    _gemini_client_generations[id(client)] = generation
    return record


def _unregister_generation(record: GeminiGenerationRecord) -> None:
    if record.lease_count:
        raise RuntimeError("Cannot unregister a generation with active leases.")
    if _gemini_generation_records.get(record.generation) is not record:
        return
    if _current_gemini_generation == record.generation:
        raise RuntimeError("Cannot unregister the current Gemini generation.")
    _gemini_generation_records.pop(record.generation, None)
    if _gemini_client_generations.get(id(record.client)) == record.generation:
        _gemini_client_generations.pop(id(record.client), None)


def _retire_generation(record):
    record.retired = True


async def _close_generation_record(record):
    if (
        not record.retired
        or record.lease_count
        or record.close_started
        or record.close_completed
    ):
        return

    record.close_started = True
    try:
        result = record.client.close()
        if inspect.isawaitable(result):
            await result
        record.close_completed = True
        _gemini_generation_records.pop(record.generation, None)
        if _gemini_client_generations.get(id(record.client)) == record.generation:
            _gemini_client_generations.pop(id(record.client), None)
    except Exception as e:
        logger.warning(f"Error closing retired Gemini client: {e}")


def acquire_current_gemini_lease() -> GeminiClientLease:
    # ponytail: synchronous counter mutations avoid adding another lock to request paths.
    if _gemini_shutdown_started:
        raise RuntimeError("Gemini client lifecycle is shutting down.")
    record = _get_current_generation_record_strict()
    if record is None:
        raise GeminiClientNotInitializedError(
            _initialization_error
            or "Gemini client was not initialized. Check logs for details."
        )
    if record.retired:
        raise GeminiGenerationUnavailableError("Current Gemini client generation is retired.")
    record.lease_count += 1
    return GeminiClientLease(record)


def acquire_gemini_lease(*, client, generation: int) -> GeminiClientLease:
    if _gemini_shutdown_started:
        raise RuntimeError("Gemini client lifecycle is shutting down.")
    record = _gemini_generation_records.get(generation)
    if record is None or record.client is not client:
        raise GeminiGenerationUnavailableError("Gemini client and generation do not match.")
    if record.retired:
        raise GeminiGenerationUnavailableError("Gemini client generation is retired.")
    record.lease_count += 1
    return GeminiClientLease(record)


def is_gemini_generation_registered(*, client, generation: int) -> bool:
    record = _gemini_generation_records.get(generation)
    return record is not None and record.client is client


def get_gemini_client_auth_source():
    """
    Return the currently selected WebAPI auth source label, if known.
    """
    return _gemini_client_auth_source


async def init_gemini_client(
    *,
    registry_updater: Optional[Callable[[MyGeminiClient, int], Awaitable[None]]] = None,
) -> bool:
    """
    Initialize and set up the Gemini client based on the configuration and canonical storage.
    Returns True on success, False on failure.
    """
    global _gemini_client, _initialization_error, _gemini_client_auth_source
    global _current_gemini_generation
    
    async with _gemini_client_init_lock:
        old_client = _gemini_client
        old_record = _get_current_generation_record_strict() if old_client is not None else None
        next_generation = (
            old_record.generation + 1
            if old_record is not None
            else (max(_gemini_generation_records) + 1 if _gemini_generation_records else 0)
        )
        _initialization_error = None
        if old_client is None:
            _gemini_client_auth_source = None

        async def publish_candidate(candidate, auth_source: str) -> bool:
            global _gemini_client, _gemini_client_auth_source, _initialization_error
            global _current_gemini_generation, _gemini_shutdown_started
            same_client = old_record is not None and candidate is old_record.client
            committed_generation = old_record.generation if same_client else next_generation
            candidate_record = None

            if not same_client:
                try:
                    candidate_record = _register_generation(candidate, committed_generation)
                except Exception as e:
                    logger.error(f"Gemini client generation registration failed: {e}", exc_info=True)
                    if old_client is None:
                        _initialization_error = str(e)
                        _gemini_client_auth_source = None
                    try:
                        await candidate.close()
                    except Exception as close_error:
                        logger.warning(f"Error closing failed Gemini replacement: {close_error}")
                    return False

            if registry_updater is not None:
                try:
                    await registry_updater(candidate, committed_generation)
                except Exception as e:
                    logger.error(f"Gemini client replacement registry update failed: {e}", exc_info=True)
                    if candidate_record is not None:
                        _unregister_generation(candidate_record)
                    _initialization_error = None if old_client is not None else str(e)
                    if not same_client:
                        try:
                            await candidate.close()
                        except Exception as close_error:
                            logger.warning(f"Error closing failed Gemini replacement: {close_error}")
                    return False

            if same_client:
                _gemini_client_auth_source = auth_source
                return True

            record = candidate_record
            _gemini_client = candidate
            _gemini_client_auth_source = auth_source
            _current_gemini_generation = record.generation
            _gemini_shutdown_started = False
            if old_record is not None:
                _retire_generation(old_record)
                await _close_generation_record(old_record)
            return True

        if not CONFIG.getboolean("EnabledAI", "gemini", fallback=True):
            error_msg = "Gemini client is disabled in config."
            logger.info(error_msg)
            _gemini_client = None
            _gemini_client_auth_source = None
            _initialization_error = error_msg
            _current_gemini_generation = None
            if old_record is not None:
                _retire_generation(old_record)
                await _close_generation_record(old_record)
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
                                return await publish_candidate(client, candidate.source_name)
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
                            return await publish_candidate(client, "browser cookie fallback")
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
            if old_client is None and best_client is not None:
                logger.info("No fully authenticated AVAILABLE session found. Retaining the guest-mode client fallback candidate.")
                return await publish_candidate(best_client, best_client_source_name)

            if old_client is not None and best_client is not None:
                logger.warning("No improved authenticated Gemini client found; preserving existing client.")
                await best_client.close()
                return False

            # If we got here, all attempts failed
            error_msg = "Gemini cookies not found or completely invalid in canonical store, legacy config, or browser."
            logger.error(error_msg)
            if old_client is None:
                _initialization_error = error_msg
                _gemini_client_auth_source = None
            return False

        except Exception as e:
            error_msg = f"Unexpected error initializing Gemini client waterfall: {e}"
            logger.error(error_msg, exc_info=True)
            if old_client is None:
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
            
            if old_client is None:
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


async def close_gemini_client() -> None:
    """Close all process-global Gemini clients and clear their state."""
    global _gemini_client, _initialization_error, _gemini_client_auth_source
    global _current_gemini_generation, _gemini_shutdown_started

    async with _gemini_client_init_lock:
        current_client = _gemini_client
        try:
            _get_current_generation_record_strict()
        except RuntimeError:
            pass
        records = list(_gemini_generation_records.values())
        represented_current_client = any(
            current_client is not None and record.client is current_client
            for record in records
        )

        _gemini_shutdown_started = True
        _gemini_client = None
        _gemini_client_auth_source = None
        _initialization_error = None
        _current_gemini_generation = None

        close_records = []
        for record in records:
            _retire_generation(record)
            if record.lease_count == 0:
                close_records.append(record)

    for record in close_records:
        await _close_generation_record(record)

    if current_client is not None and not represented_current_client:
        try:
            result = current_client.close()
            if inspect.isawaitable(result):
                await result
        except Exception as e:
            logger.warning(f"Error closing untracked Gemini client during shutdown: {e}")
