import asyncio
import re
from typing import Optional, Any
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError
from app.services.browser.base_adapter import BaseProviderAdapter
from app.services.browser.adapters.scripts.gemini_scripts import SELECTORS
from app.logger import logger
from app.services.browser.errors import TransientSessionError, ModelNotFoundError, GatedModelError

class GeminiProviderAdapter(BaseProviderAdapter):
    """
    Concrete adapter for the Google Gemini Web interface.
    Implements only the minimal DOM selectors, form inputs, URL parsing,
    and authentication heuristics, with zero changes to orchestration.
    """
    def __init__(self, ui_wait_timeout: int = 15000):
        self.ui_wait_timeout = ui_wait_timeout

    @property
    def provider_name(self) -> str:
        return "gemini"

    async def check_authentication(self, page: Page) -> bool:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError
        try:
            if "accounts.google.com" in page.url and "/signin" in page.url:
                return False
            signin_button = page.get_by_role("button", name=re.compile(r"sign in", re.IGNORECASE))
            try:
                count = await signin_button.count()
                if count > 0:
                    if await signin_button.first.is_visible():
                        return False
            except (PlaywrightTimeoutError, PlaywrightError, asyncio.TimeoutError) as e:
                # Only known transient Playwright/connection issues are transient
                raise TransientSessionError(f"Transient error during authentication DOM check: {e}") from e
            return True
        except TransientSessionError:
            raise
        except (PlaywrightTimeoutError, PlaywrightError, asyncio.TimeoutError) as e:
            # Outer navigation or target closure Playwright issues are transient
            raise TransientSessionError(f"Transient failure during authentication navigation check: {e}") from e

    def extract_conversation_id(self, url: str) -> Optional[str]:
        match = re.search(r"/app/([a-z0-9]+)", url)
        if match:
            return match.group(1)
        return None

    async def submit_prompt(self, page: Page, prompt: str, state: Optional[Any] = None) -> bool:
        # 1. Historical Marking (Response Ownership)
        await page.evaluate(
            f"() => document.querySelectorAll('{SELECTORS['RESPONSE_CONTAINER']}').forEach(el => el.setAttribute('data-gemini-historical', 'true'))"
        )
        
        if state and hasattr(state, "active_tab") and state.active_tab:
            state.active_tab.heartbeat("prompt_fill")
            
        input_locator = page.locator(SELECTORS["INPUT"]).first
        await input_locator.click()
        await input_locator.focus()
        await input_locator.fill(prompt)
        await page.keyboard.press("End")
        await asyncio.sleep(0.1)
        
        submit_button = page.get_by_role("button", name=re.compile("Send", re.I)).first
        if await submit_button.count() == 0:
            submit_button = page.locator(SELECTORS["SEND_BUTTON"]).first

        await submit_button.wait_for(state="visible", timeout=self.ui_wait_timeout)
        
        if not await submit_button.is_enabled():
            await page.keyboard.press("Space")
            await page.keyboard.press("Backspace")
            await asyncio.sleep(0.1)
            
        confirmed = False
        for attempt in range(2):
            if state and hasattr(state, "submission_confirmed") and state.submission_confirmed:
                if state.submission_confirmed.is_set():
                    confirmed = True
                    break
                state.submission_confirmed.clear()
                
            if await submit_button.is_enabled():
                await submit_button.click()
            else:
                await page.keyboard.press("Enter")
            
            if state and hasattr(state, "submission_confirmed") and state.submission_confirmed:
                try:
                    # Short-lived wait while holding submit_lock
                    async with asyncio.timeout(3.5):
                        await state.submission_confirmed.wait()
                        confirmed = True
                        break
                except asyncio.TimeoutError:
                    if attempt == 0:
                        logger.warning("Submission not confirmed, retrying...", extra={"request_id": getattr(state, "request_id", "unknown")})
                        continue
            else:
                # If no state or event, assume submitted immediately (stateless fallback)
                confirmed = True
                break
                
        return confirmed

    async def get_active_model(self, page: Page) -> Optional[str]:
        """
        Detect the currently active Gemini model from the UI.
        Returns the simplified label (e.g., 'Flash', 'Pro') or None if undetected.
        """
        picker = page.locator(SELECTORS["MODEL_PICKER"]).first
        if await picker.count() == 0:
            return None
        
        # Try to extract from aria-label first (most reliable)
        aria_label = await picker.get_attribute("aria-label")
        if aria_label and "currently " in aria_label:
            return aria_label.split("currently ")[-1].strip()
        
        # Fallback to button text
        text = await picker.inner_text()
        if text:
            return text.strip()
            
        return None

    async def select_model(self, page: Page, requested_model_label: str, state: Optional[Any] = None) -> None:
        """
        Explicitly select a Gemini model via the UI picker.
        Fails fast if the model is not found or selection verification fails.
        """
        active_model = await self.get_active_model(page)
        if active_model and requested_model_label.lower() in active_model.lower():
            logger.debug(f"Model selection no-op: '{active_model}' already active.", extra={"request_id": getattr(state, "request_id", "unknown")})
            return

        logger.info(f"Switching Gemini model: '{active_model}' -> '{requested_model_label}'", extra={"request_id": getattr(state, "request_id", "unknown")})
        
        picker = page.locator(SELECTORS["MODEL_PICKER"]).first
        if await picker.count() == 0:
            raise TransientSessionError("Gemini model picker not found in the UI.")

        # 1. Open Picker
        await picker.click()
        
        # 2. Wait for options to become visible (replaces asyncio.sleep)
        try:
            await page.locator(SELECTORS["MODEL_OPTION"]).first.wait_for(state="visible", timeout=3000)
        except PlaywrightTimeoutError:
            raise TransientSessionError("Gemini model options menu failed to open or items are not visible.")
        
        # 3. Find and click option
        options = page.locator(SELECTORS["MODEL_OPTION"])
        option_count = await options.count()
        
        target_option = None
        found_labels = []
        for i in range(option_count):
            opt = options.nth(i)
            label = await opt.inner_text()
            found_labels.append(label.strip().replace("\n", " "))
            if requested_model_label.lower() in label.lower():
                target_option = opt
                break
        
        if not target_option:
            # Check for paywalls or gated models in the content
            page_content = await page.content()
            if "Try Gemini Advanced" in page_content and requested_model_label.lower() == "pro":
                raise GatedModelError(
                    f"Requested model '{requested_model_label}' is gated behind a Gemini Advanced subscription."
                )
            
            raise ModelNotFoundError(
                f"Requested Gemini model '{requested_model_label}' not found in the picker menu. "
                f"Available options: {found_labels}"
            )

        await target_option.click()
        
        # 4. Verify Selection (polling with short timeout)
        verification_timeout = 3.0
        poll_interval = 0.5
        elapsed = 0.0
        success = False
        last_found = None
        
        while elapsed < verification_timeout:
            new_active = await self.get_active_model(page)
            last_found = new_active
            if new_active and requested_model_label.lower() in new_active.lower():
                success = True
                break
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        if not success:
            raise TransientSessionError(
                f"Gemini model selection verification failed. Requested: '{requested_model_label}', Found: '{last_found}'"
            )
        
        logger.info(f"Gemini model successfully switched to '{last_found}'", extra={"request_id": getattr(state, "request_id", "unknown")})
