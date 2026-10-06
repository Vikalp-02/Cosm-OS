"""Build the language model the app is configured to use, if any."""

from __future__ import annotations

from cosmos.app.settings import Settings
from cosmos.llm import LLM
from cosmos.llm.claude import DEFAULT_MODEL as CLAUDE_DEFAULT_MODEL
from cosmos.llm.claude import ClaudeLLM
from cosmos.llm.openai_compatible import (
    GEMINI_BASE_URL,
    GEMINI_DEFAULT_MODEL,
    GEMINI_FALLBACK_MODELS,
    OpenAICompatibleLLM,
)


def make_llm(settings: Settings) -> LLM | None:
    """The configured model, or None when the AI features are switched off."""
    provider = settings.resolved_llm_provider
    timeout = settings.llm_timeout_seconds

    if provider == "anthropic":
        key = settings.anthropic_api_key
        return ClaudeLLM(
            model=settings.llm_model or CLAUDE_DEFAULT_MODEL,
            timeout_seconds=timeout,
            # With no key here, the SDK resolves credentials from the environment.
            api_key=key.get_secret_value() if key else None,
        )
    if provider == "gemini":
        if not settings.gemini_api_key:
            raise ValueError("the Gemini provider needs GEMINI_API_KEY")
        return OpenAICompatibleLLM(
            base_url=GEMINI_BASE_URL,
            api_key=settings.gemini_api_key.get_secret_value(),
            model=settings.llm_model or GEMINI_DEFAULT_MODEL,
            timeout_seconds=timeout,
            fallback_models=(
                GEMINI_FALLBACK_MODELS
                if settings.llm_fallback_models is None
                else settings.llm_fallback_models
            ),
            send_effort=True,
        )
    if provider == "openai_compatible":
        if not (settings.llm_base_url and settings.llm_api_key and settings.llm_model):
            raise ValueError(
                "an OpenAI-compatible provider needs COSMOS_LLM_BASE_URL,"
                " COSMOS_LLM_API_KEY and COSMOS_LLM_MODEL"
            )
        return OpenAICompatibleLLM(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value(),
            model=settings.llm_model,
            timeout_seconds=timeout,
            fallback_models=settings.llm_fallback_models or (),
        )
    return None
