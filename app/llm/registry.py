from collections.abc import Callable

from app.config import PROVIDERS, Settings, get_settings
from app.llm.base import LLMError, LLMProvider

# Tests swap this for a fake provider factory
_override: Callable[[str, str], LLMProvider] | None = None


def set_provider_override(factory: Callable[[str, str], LLMProvider] | None) -> None:
    global _override
    _override = factory


def get_provider(
    provider: str, model: str | None = None, purpose: str = "classify", settings: Settings | None = None
) -> LLMProvider:
    settings = settings or get_settings()
    if provider not in PROVIDERS:
        raise LLMError(f"Unknown LLM provider {provider!r}")
    if purpose == "profile":
        # The per-user model override applies to classification; profile refinement
        # uses the provider's (usually stronger) profile model unless none is set.
        model = settings.default_model(provider, "profile") or model
    model = model or settings.default_model(provider, purpose)
    if _override:
        return _override(provider, model)
    if not settings.provider_configured(provider) or not model:
        raise LLMError(f"LLM provider {provider!r} is not configured (check .env)")

    if provider == "anthropic":
        from app.llm.anthropic import AnthropicProvider

        return AnthropicProvider(settings.anthropic_api_key, model)
    if provider == "gemini":
        from app.llm.gemini import GeminiProvider

        return GeminiProvider(settings.gemini_api_key, model)

    from app.llm.openai_compat import OpenAICompatProvider

    if provider == "openai":
        return OpenAICompatProvider("openai", model, settings.openai_api_key)
    return OpenAICompatProvider("ollama", model, "ollama", base_url=settings.ollama_base_url)
