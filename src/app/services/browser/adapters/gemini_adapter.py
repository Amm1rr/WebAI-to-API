import asyncio
import re
from typing import Optional, Any
from playwright.async_api import Page
from app.services.browser.base_adapter import BaseProviderAdapter
from app.services.browser.adapters.scripts.gemini_scripts import SELECTORS
from app.logger import logger

class GeminiProviderAdapter(BaseProviderAdapter):
    """
    Concrete adapter for the Google Gemini Web interface.
    Implements only the minimal DOM selectors, form inputs, URL parsing,
    and authentication heuristics, with zero changes to orchestration.
    """
    @property
    def provider_name(self) -> str:
        return "gemini"

    async def check_authentication(self, page: Page) -> bool:
        try:
            if "accounts.google.com" in page.url and "/signin" in page.url:
                return False
            signin_button = page.get_by_role("button", name=re.compile(r"sign in", re.IGNORECASE)).first
            try:
                visible = await asyncio.wait_for(
                    signin_button.evaluate("el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)"),
                    timeout=1.5
                )
                if visible:
                    return False
            except (asyncio.TimeoutError, Exception):
                pass
            return True
        except Exception as e:
            if "Target closed" in str(e):
                return True
            return True

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

        await submit_button.wait_for(state="visible", timeout=5000)
        
        if not await submit_button.is_enabled():
            await page.keyboard.press("Space")
            await page.keyboard.press("Backspace")
            await asyncio.sleep(0.1)
            
        confirmed = False
        for attempt in range(2):
            if state and hasattr(state, "submission_confirmed") and state.submission_confirmed:
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
