import asyncio
import re
from typing import Optional, Any
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError
from app.services.browser.base_adapter import BaseProviderAdapter
from app.services.providers.gemini.scripts.gemini_scripts import SELECTORS, MODEL_PICKER_FALLBACK_SELECTORS
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

    async def _resolve_extended_thinking_item(self, page: Page) -> Any:
        """
        Identify the Extended Thinking menu item without relying on UI text.

        Structural signal: inside [data-test-id="gem-mode-menu"], Extended
        Thinking is the only gem-menu-item lacking a data-mode-id (model modes
        own one; the toggle is a mode modifier). When zero or multiple
        candidates match, fall back to the English role/name matcher.
        """
        menu = page.locator('[role="menu"][data-test-id="gem-mode-menu"]').first
        structural = menu.locator('gem-menu-item[role="menuitem"]:not([data-mode-id])')
        if await structural.count() == 1:
            return structural.first
        return page.get_by_role("menuitem", name=re.compile("Extended thinking", re.I)).first

    async def set_extended_thinking(self, page: Page, enabled: bool, state: Optional[Any] = None) -> None:
        """Normalize Gemini mode-picker Extended thinking state for one request.

        Detection is language-independent when possible (structural item lookup,
        English fallback). An absent control after the mode menu opened means the
        feature is unavailable; that satisfies a requested OFF state. Capability
        gating uses aria-disabled; ON/OFF state lives in the `selected` class.
        """
        await self._open_mode_menu(page)
        item = await self._resolve_extended_thinking_item(page)
        try:
            await item.wait_for(state="visible", timeout=1000)
        except PlaywrightTimeoutError as error:
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            if not enabled:
                return
            raise ModelNotFoundError("Gemini Extended thinking control is unavailable.") from error

        aria_disabled = await item.get_attribute("aria-disabled")
        selected = "selected" in (await item.get_attribute("class") or "").split()
        if selected == enabled:
            await page.keyboard.press("Escape")
            return
        if aria_disabled == "true":
            await page.keyboard.press("Escape")
            raise GatedModelError("Gemini Extended thinking is disabled for this model or account.")

        await item.click()

        for _ in range(12):
            try:
                await self._open_mode_menu(page)
                verified_item = await self._resolve_extended_thinking_item(page)
                await verified_item.wait_for(state="visible", timeout=1000)
                verified = "selected" in (await verified_item.get_attribute("class") or "").split()
                await page.keyboard.press("Escape")
            except PlaywrightTimeoutError:
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass
                raise TransientSessionError("Gemini Extended thinking state verification failed.")
            if verified == enabled:
                return
            await asyncio.sleep(0.25)

        raise TransientSessionError("Gemini Extended thinking state transition could not be verified.")

    async def _open_mode_menu(self, page: Page) -> Any:
        picker = await self._find_model_picker(page)
        if not picker:
            raise TransientSessionError("Gemini mode picker is unavailable while configuring Extended thinking.")

        await picker.click()
        menu = page.locator('[role="menu"][data-test-id="gem-mode-menu"]').first
        try:
            await menu.wait_for(state="visible", timeout=3000)
        except PlaywrightTimeoutError as error:
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            raise TransientSessionError("Gemini mode picker menu failed to render.") from error
        return menu

    async def _find_model_picker(self, page: Page) -> Optional[Any]:
        """
        Locate the model picker button using primary and fallback selectors.
        """
        primary = SELECTORS["MODEL_PICKER"]
        
        # Try primary first
        picker = page.locator(primary).first
        if await picker.count() > 0:
            return picker
            
        # Try fallbacks
        for selector in MODEL_PICKER_FALLBACK_SELECTORS:
            picker = page.locator(selector).first
            if await picker.count() > 0:
                return picker
                
        return None

    async def get_active_model(self, page: Page) -> Optional[str]:
        """
        Detect the currently active Gemini model from the UI.
        Returns the simplified label (e.g., 'Flash', 'Pro') or None if undetected.
        """
        picker = await self._find_model_picker(page)
        if not picker:
            return None
        
        # Try to extract from aria-label first (most reliable)
        aria_label = await picker.get_attribute("aria-label")
        if aria_label and "currently " in aria_label:
            return aria_label.split("currently ")[-1].strip()
        
        # Fallback to button text
        text = await picker.inner_text()
        if text:
            # Often contains version numbers like "3.5 Flash", we want to see if our label is in it
            return text.strip().replace("\n", " ")
            
        return None

    def is_correct_model_match(self, requested_label: str, ui_label: str) -> bool:
        """
        Helper to determine if a UI label matches a requested model label,
        handling collisions like 'Flash' vs 'Flash-Lite'.
        """
        req = requested_label.lower().strip()
        ui = ui_label.lower().strip()
        
        # Space/Hyphen normalization
        req_norm = req.replace("-", " ")
        ui_norm = ui.replace("-", " ")
        
        # Basic substring check
        if req not in ui and req_norm not in ui_norm:
            return False
            
        # Collision prevention: 'Flash' should NOT match 'Flash-Lite'
        # If request is 'Flash' (no 'lite') but UI has 'lite', it's NOT a match.
        if "flash" in req and "lite" not in req and "lite" in ui:
            return False
            
        return True

    async def select_model(self, page: Page, requested_model_label: str, state: Optional[Any] = None) -> None:
        """
        Explicitly select a Gemini model via the UI picker.
        Fails fast if the model is not found or selection verification fails.
        """
        # Polling/Wait for picker to appear (UI stabilization)
        max_wait = 5.0
        interval = 0.5
        elapsed = 0.0
        picker = None
        
        while elapsed < max_wait:
            picker = await self._find_model_picker(page)
            if picker:
                break
            await asyncio.sleep(interval)
            elapsed += interval

        if not picker:
            # Diagnostics for the error message
            title = await page.title()
            url = page.url
            buttons = await page.query_selector_all('button')
            labels = []
            for btn in buttons[:10]: # limit noise
                label = await btn.get_attribute("aria-label")
                if label: labels.append(label)
            
            raise TransientSessionError(
                f"Gemini model picker not found in the UI. "
                f"URL: {url}, Title: '{title}', Candidate labels: {labels}"
            )

        active_model = await self.get_active_model(page)
        if active_model and self.is_correct_model_match(requested_model_label, active_model):
            logger.debug(f"Model selection no-op: '{active_model}' already active.", extra={"request_id": getattr(state, "request_id", "unknown")})
            return

        logger.info(f"Switching Gemini model: '{active_model}' -> '{requested_model_label}'", extra={"request_id": getattr(state, "request_id", "unknown")})
        
        # Ensure picker is in view and ready
        await picker.scroll_into_view_if_needed()
        await picker.click()
        
        # 2. Wait for options to become visible (replaces asyncio.sleep)
        try:
            await page.locator(SELECTORS["MODEL_OPTION"]).first.wait_for(state="visible", timeout=3000)
        except PlaywrightTimeoutError:
            raise TransientSessionError("Gemini model options menu failed to open or items are not visible.")
        
        # 3. Find and click option
        options = page.locator(SELECTORS["MODEL_OPTION"])
        option_count = await options.count()
        
        # Normalize requested label
        req_lower = requested_model_label.lower().strip()
        req_norm = req_lower.replace("-", " ")
        
        candidates = []
        for i in range(option_count):
            opt = options.nth(i)
            label = (await opt.inner_text()).strip().replace("\n", " ")
            if not label:
                continue
                
            label_lower = label.lower()
            label_norm = label_lower.replace("-", " ")
            
            # Check for any kind of match
            is_match = req_lower in label_lower or req_norm in label_norm
            if not is_match:
                continue
                
            # Score the match
            score = 0
            # Priority 1: Exact label match (highest)
            if req_lower == label_lower or req_norm == label_norm:
                score += 100
            
            # Priority 2: Collision Prevention (very important)
            # If we want 'Flash' but NOT 'Lite', and the label has 'Lite', penalize heavily
            is_lite_request = "lite" in req_lower
            is_lite_label = "lite" in label_lower
            if not is_lite_request and is_lite_label:
                score -= 50
            elif is_lite_request and is_lite_label:
                score += 20
                
            # Priority 3: Contains the exact version requested (if any)
            # (Future-proofing for when we might want to be more specific)
            
            candidates.append({
                "index": i,
                "label": label,
                "score": score,
                "locator": opt,
                "mode_id": await opt.get_attribute("data-mode-id"),
            })

        if not candidates:
            # Diagnostics for the error message
            page_content = await page.content()
            if "Try Gemini Advanced" in page_content and requested_model_label.lower() == "pro":
                raise GatedModelError(
                    f"Requested model '{requested_model_label}' is gated behind a Gemini Advanced subscription."
                )
            
            # Re-collect labels for the error message
            found_labels = []
            for i in range(option_count):
                found_labels.append(await options.nth(i).inner_text())

            raise ModelNotFoundError(
                f"Requested Gemini model '{requested_model_label}' not found in the picker menu. "
                f"Available options: {found_labels}"
            )

        # Sort candidates by score (descending)
        candidates.sort(key=lambda x: x["score"], reverse=True)
        target_option = candidates[0]["locator"]
        target_mode_id = candidates[0]["mode_id"]

        await target_option.click()

        if target_mode_id:
            await self._verify_selection_by_mode_id(page, requested_model_label, target_mode_id, state)
        else:
            await self._verify_selection_by_label(page, requested_model_label, state)

        logger.info(f"Gemini model successfully switched to '{requested_model_label}'", extra={"request_id": getattr(state, "request_id", "unknown")})

    async def _verify_selection_by_mode_id(self, page: Page, requested_model_label: str, mode_id: str, state: Optional[Any] = None) -> None:
        """
        Verify the clicked option via its session-captured data-mode-id: after
        reopening the menu exactly one item must carry that id and the `selected`
        class. `data-active`/`active` are focus artifacts and never count.
        The id is re-read from the same DOM moments after the click, so it cannot
        go stale like a hardcoded mapping would.
        """
        verification_timeout = 3.0
        poll_interval = 0.5
        elapsed = 0.0

        while elapsed < verification_timeout:
            await self._open_mode_menu(page)
            matches = page.locator(f'gem-menu-item[data-mode-id="{mode_id}"]')
            match_count = await matches.count()
            if match_count != 1:
                await page.keyboard.press("Escape")
                raise TransientSessionError(
                    f"Gemini model selection verification is ambiguous. Requested: '{requested_model_label}', "
                    f"matching options for id '{mode_id}': {match_count}"
                )
            selected_class = (await matches.nth(0).get_attribute("class")) or ""
            if "selected" in selected_class.split():
                await page.keyboard.press("Escape")
                return
            # Close the menu before backoff so the next poll's picker click
            # reopens it instead of toggling a still-open menu shut.
            await page.keyboard.press("Escape")
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        raise TransientSessionError(
            f"Gemini model selection verification failed. Requested: '{requested_model_label}', "
            f"option '{mode_id}' never became selected."
        )

    async def _verify_selection_by_label(self, page: Page, requested_model_label: str, state: Optional[Any] = None) -> None:
        """Legacy text-based verification fallback when no data-mode-id exists."""
        verification_timeout = 3.0
        poll_interval = 0.5
        elapsed = 0.0
        success = False
        last_found = None
        
        while elapsed < verification_timeout:
            new_active = await self.get_active_model(page)
            last_found = new_active
            if new_active and self.is_correct_model_match(requested_model_label, new_active):
                success = True
                break
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        if not success:
            raise TransientSessionError(
                f"Gemini model selection verification failed. Requested: '{requested_model_label}', Found: '{last_found}'"
            )
