# src/app/services/providers/gemini/auth.py
import os
import asyncio
from typing import Dict, Any, Optional
from app.config import CONFIG, get_default_auth_state_dir
from app.logger import logger
from app.services.browser.auth_types import AuthStatus

class GeminiAuthStrategy:
    """
    Gemini-specific authentication workflow strategy.
    Implements login flows, status checks, and recovery for Google Gemini.
    """

    def get_state_path(self) -> str:
        auth_state_dir = CONFIG["Playwright"].get("auth_state_dir", get_default_auth_state_dir())
        return os.path.join(auth_state_dir, "gemini.json")

    def refresh_status(self) -> Dict[str, Any]:
        """
        Perform lightweight status checks for Gemini.
        Returns a dict with 'playwright' and 'webapi' status strings.
        """
        from app.services.providers.gemini.auth_selector import GeminiAuthSelector
        
        # 1. Check Playwright/JSON status
        candidates = list(GeminiAuthSelector.iter_candidates())
        auth_candidate = next((c for c in candidates if c.supports_playwright_storage), None)
        
        is_legacy = any(c.is_legacy for c in candidates)
        playwright_status = AuthStatus.VALID_SESSION if auth_candidate else AuthStatus.NO_SESSION
        
        # 2. Check direct WebAPI client status
        webapi_status = AuthStatus.INVALID
        try:
            import app.services.providers.gemini.client as gc
            client_instance = gc._gemini_client
            if client_instance:
                status_name = client_instance.client.account_status.name if hasattr(client_instance.client, 'account_status') else "UNKNOWN"
                if status_name == "AVAILABLE":
                    webapi_status = AuthStatus.AUTHENTICATED
                elif status_name == "UNAUTHENTICATED":
                    webapi_status = AuthStatus.GUEST
        except Exception as e:
            logger.debug(f"GeminiAuthStrategy: Error reading webapi status: {e}")

        return {
            "playwright": playwright_status,
            "webapi": webapi_status,
            "is_legacy": is_legacy
        }

    async def run_login_flow(self, engine) -> None:
        """
        Orchestrate the headful login workflow for Gemini.
        """
        from app.services.browser.adapters.scripts.gemini_scripts import SELECTORS
        
        # Robust selectors for verification
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

        logger.info("GeminiAuthStrategy: Launching isolated bootstrap browser...")
        page_wrapper = await engine.get_page("gemini", enable_persistence=True)
        page = page_wrapper.page
        session = await engine.get_session("gemini", enable_persistence=True)
        
        login_detected = False
        user_closed = False
        last_state = None
        
        try:
            logger.info("GeminiAuthStrategy: Navigating to https://gemini.google.com/app...")
            await page.goto("https://gemini.google.com/app")
            
            # Poll every 2 seconds for a maximum of 5 minutes (150 iterations)
            for _ in range(150):
                if page.is_closed():
                    user_closed = True
                    break
                
                current_url = page.url
                is_google_login = "accounts.google.com" in current_url
                
                has_sign_in_button = False
                for selector in SIGN_IN_SELECTORS:
                    try:
                        if await page.locator(selector).first.is_visible():
                            has_sign_in_button = True
                            break
                    except Exception:
                        pass
                
                input_visible = await page.locator(SELECTORS["INPUT"]).first.is_visible()
                
                has_auth_indicator = False
                for selector in AUTHENTICATED_SELECTORS:
                    try:
                        if await page.locator(selector).first.is_visible():
                            has_auth_indicator = True
                            break
                    except Exception:
                        pass

                if is_google_login:
                    current_state = "sign_in_page_detected"
                elif input_visible and not has_sign_in_button and "gemini.google.com" in current_url:
                    current_state = "authenticated_chat_detected"
                else:
                    current_state = "waiting_for_user_login"

                if current_state != last_state:
                    logger.info(f"GeminiAuthStrategy Status: {current_state}")
                    last_state = current_state

                if current_state == "authenticated_chat_detected":
                    login_detected = True
                    await session.save_state()
                    logger.info("GeminiAuthStrategy: Success! Google sign-in detected and state saved atomically.")
                    break

                await asyncio.sleep(2)
        except Exception as e:
            err_msg = str(e).lower()
            if (
                "target closed" in err_msg
                or "page closed" in err_msg
                or "context closed" in err_msg
            ):
                logger.warning(f"GeminiAuthStrategy: Browser closure detected: {e}")
                user_closed = True
            else:
                logger.error(f"GeminiAuthStrategy: Unexpected exception during login monitoring: {e}")
                raise
        finally:
            if page_wrapper:
                try:
                    await page_wrapper.close()
                except Exception as e:
                    logger.debug(f"GeminiAuthStrategy: page wrapper cleanup ignored: {e}")

        if not login_detected:
            if user_closed:
                raise RuntimeError("Interactive sign-in was closed by user.")
            else:
                raise TimeoutError("Interactive sign-in timed out.")

    async def run_post_login_recovery(self) -> None:
        """
        Ordered recovery for Gemini components after successful login.
        """
        from app.services.providers.gemini.client import init_gemini_client
        from app.services.providers.gemini.session_manager import init_session_managers
        from app.services.factory import ProviderFactory
        
        logger.info("GeminiAuthStrategy: Clearing and closing registered Gemini provider in ProviderFactory...")
        await ProviderFactory.close_provider("gemini")

        logger.info("GeminiAuthStrategy: Re-initializing direct Gemini WebAPI client...")
        init_success = await init_gemini_client()
        if not init_success:
            raise RuntimeError("Gemini direct client initialization returned False.")
        
        logger.info("GeminiAuthStrategy: Re-initializing session managers with new client...")
        await init_session_managers()
