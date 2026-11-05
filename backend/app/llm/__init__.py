"""LLM providers. Pick one with `LLM_PROVIDER`; each returns raw JSON text."""

from app.config import get_settings
from app.llm.base import LLMProvider


def get_provider() -> LLMProvider:
    settings = get_settings()
    name = settings.llm_provider
    if name == "gemini":
        from app.llm.gemini import GeminiProvider

        return GeminiProvider(settings.llm_model)
    if name == "openai":
        from app.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(settings.llm_model)
    if name == "anthropic":
        from app.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(settings.llm_model)
    if name == "mock":
        from app.llm.mock import MockProvider

        return MockProvider()
    raise ValueError(f"Unknown LLM_PROVIDER: {name!r}")
