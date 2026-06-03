from app.schemas.request import OpenAIChatRequest
from app.services.factory import ProviderFactory
from app.services.providers.gemini.shared import PLAYWRIGHT_GEMINI_MODEL_UI_LABELS


def is_legacy_playwright_gemini_alias(model_id: str) -> bool:
    if not model_id.startswith("playwright/"):
        return False
    if model_id.startswith("playwright/gemini/"):
        return False

    alias_name = model_id[len("playwright/"):]
    return alias_name in PLAYWRIGHT_GEMINI_MODEL_UI_LABELS


def filter_advertised_model_catalog(models: list[dict[str, object]]) -> list[dict[str, object]]:
    return [model for model in models if not is_legacy_playwright_gemini_alias(str(model.get("id", "")))]


async def list_models(*, include_legacy_playwright_aliases: bool = True) -> dict[str, object]:
    """Build the shared model catalog.

    UI callers keep legacy aliases for backward-compatible selection.
    The public /v1/models route disables them to advertise only canonical IDs.
    """
    all_models = []
    for provider_key in ProviderFactory._registry.keys():
        dummy_request = OpenAIChatRequest(messages=[], provider=provider_key)
        provider, _ = ProviderFactory.get_provider(dummy_request)
        all_models.extend(await provider.list_models())

    if not include_legacy_playwright_aliases:
        all_models = filter_advertised_model_catalog(all_models)

    return {
        "object": "list",
        "data": all_models,
    }
