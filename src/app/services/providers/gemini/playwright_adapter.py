import asyncio
from typing import Any

from fastapi import HTTPException
from playwright.async_api import Error as PlaywrightError, Page, TimeoutError as PlaywrightTimeoutError

from app.services.browser.engine import get_browser_engine
from app.services.providers.gemini.browser_adapter import GeminiProviderAdapter
from app.services.providers.gemini.scripts.gemini_scripts import (
    SELECTORS,
    STOP_OBSERVER_SCRIPT,
    STREAM_EXTRACTOR_SCRIPT,
)
from app.services.browser.errors import GatedModelError, ModelNotFoundError, TransientSessionError
from app.services.browser.request_executor import (
    BrowserRequestExecutor,
    BrowserRequestExecutorHooks,
    BrowserRequestConfig,
    BrowserRequestState,
)
from app.services.browser.tab import TabStatus
from app.services.providers.gemini.base_adapter import (
    GEMINI_PLAYWRIGHT_OPENAI_COMPATIBILITY,
    GeminiBackendAdapter,
)
from app.services.providers.gemini.shared import (
    PLAYWRIGHT_GEMINI_MODEL_UI_LABELS,
    convert_to_openai_format,
    resolve_extended_thinking,
)


class GeminiPlaywrightAdapter(GeminiBackendAdapter):
    """
    Compatibility wrapper for Gemini browser-native execution.

    Shared request lifecycle authority lives in BrowserRequestExecutor.
    """

    openai_compatibility = GEMINI_PLAYWRIGHT_OPENAI_COMPATIBILITY

    def __init__(self, provider):
        self.provider = provider
        self.config = BrowserRequestConfig.load()
        self.executor = BrowserRequestExecutor(
            config=self.config,
            hooks=BrowserRequestExecutorHooks(
                provider_name="gemini",
                session_name="gemini",
                callback_name="__gemini_bridge",
                bridge_callbacks_attr="_gemini_callbacks",
                default_model="playwright/gemini",
                create_browser_adapter=self._create_browser_adapter,
                get_browser_engine=self._get_browser_engine,
                sleep=self._sleep,
                timeout=self._timeout,
                navigate=self._navigate,
                wait_for_ready_ui=self._wait_for_ready_ui,
                start_observer=self._start_observer,
                stop_observer=self._stop_observer,
                stop_generation=self._stop_generation,
                extract_conversation_id=self._extract_conversation_id,
                convert_to_openai_format=convert_to_openai_format,
                orchestrate_model_selection=self._orchestrate_model_selection,
                configure_request_options=self._configure_request_options,
            ),
        )

    async def chat_completions(self, request, cid, is_new_conversation, tools_prompt) -> Any:
        return await self.executor.execute(request)

    def _create_browser_adapter(self) -> GeminiProviderAdapter:
        return GeminiProviderAdapter(ui_wait_timeout=self.config.ui_wait_timeout)

    async def _get_browser_engine(self):
        return await get_browser_engine()

    async def _sleep(self, delay: float) -> None:
        await asyncio.sleep(delay)

    def _timeout(self, delay: float):
        return asyncio.timeout(delay)

    async def _navigate(self, page: Page, state: BrowserRequestState, request, config: BrowserRequestConfig) -> None:
        if state.reused_conversation:
            target_url = f"https://gemini.google.com/app/{state.conversation_id}"
            if page.url != target_url:
                if state.active_tab:
                    state.active_tab.heartbeat("navigation_start")
                await page.goto(target_url, wait_until="domcontentloaded", timeout=config.navigation_timeout)
                if state.active_tab:
                    state.active_tab.heartbeat("navigation_end")
        elif request.conversation_id:
            if state.active_tab:
                state.active_tab.heartbeat("navigation_start")
            await page.goto(
                f"https://gemini.google.com/app/{request.conversation_id}",
                wait_until="domcontentloaded",
                timeout=config.navigation_timeout,
            )
            if state.active_tab:
                state.active_tab.heartbeat("navigation_end")
        else:
            if "gemini.google.com/app" not in page.url:
                if state.active_tab:
                    state.active_tab.heartbeat("navigation_start")
                await page.goto("https://gemini.google.com/app", wait_until="domcontentloaded", timeout=config.navigation_timeout)
                if state.active_tab:
                    state.active_tab.heartbeat("navigation_end")

    async def _wait_for_ready_ui(self, page: Page, state: BrowserRequestState, config: BrowserRequestConfig) -> None:
        input_locator = page.locator(SELECTORS["INPUT"]).first
        if state.active_tab:
            state.active_tab.heartbeat("input_wait")
        try:
            await input_locator.wait_for(state="visible", timeout=config.ui_wait_timeout)
            await asyncio.sleep(0.5)
        except (PlaywrightTimeoutError, PlaywrightError) as e:
            state.page_poisoned = True
            if state.active_tab:
                state.active_tab.status = TabStatus.DEAD
            raise TransientSessionError(f"Gemini input textbox acquisition failed: {e}") from e

    async def _start_observer(self, page: Page, callback_name: str, request_id: str) -> Any:
        return await page.evaluate(f"({STREAM_EXTRACTOR_SCRIPT})('{callback_name}', '{request_id}')")

    async def _stop_observer(self, page: Page, request_id: str) -> None:
        await page.evaluate(f"({STOP_OBSERVER_SCRIPT})('{request_id}')")

    async def _stop_generation(self, page: Page) -> None:
        stop_button = page.locator(SELECTORS["STOP_BUTTON"]).first
        if await stop_button.is_visible():
            await stop_button.click()

    async def _configure_request_options(
        self,
        browser_adapter: GeminiProviderAdapter,
        page: Page,
        request,
        state: BrowserRequestState,
    ) -> None:
        extended_thinking = resolve_extended_thinking(request)

        try:
            await browser_adapter.set_extended_thinking(page, extended_thinking, state)
        except (ModelNotFoundError, GatedModelError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    def _extract_conversation_id(self, url: str):
        return GeminiProviderAdapter().extract_conversation_id(url)

    async def _cleanup(self, observer_task, state, lease, session):
        await self.executor._cleanup(observer_task, state, lease, session)

    async def _orchestrate_model_selection(self, browser_adapter: GeminiProviderAdapter, page: Page, model_id: str, state: BrowserRequestState):
        if not model_id:
            return

        if model_id.startswith("playwright/"):
            model_id = model_id[len("playwright/"):]

        provider_name = getattr(self.provider, "provider_name", None)
        if not isinstance(provider_name, str) or not provider_name:
            provider_name = "gemini"
        if "/" in model_id:
            namespace, actual_model = model_id.split("/", 1)
            if namespace == provider_name:
                model_id = actual_model

        target_label = PLAYWRIGHT_GEMINI_MODEL_UI_LABELS.get(model_id.lower())
        if target_label:
            if state.active_tab:
                state.active_tab.heartbeat("model_selection")
            try:
                await browser_adapter.select_model(page, target_label, state)
            except GatedModelError as e:
                raise HTTPException(status_code=403, detail=str(e))
            except ModelNotFoundError as e:
                raise HTTPException(status_code=400, detail=str(e))
        elif model_id != "gemini":
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Requested model '{model_id}' has no known Playwright UI mapping. "
                    f"Supported: {list(PLAYWRIGHT_GEMINI_MODEL_UI_LABELS.keys())}"
                ),
            )

    async def close(self) -> None:
        pass
