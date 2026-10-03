"""Explicit provider selection without automatic failover."""

from __future__ import annotations

from .base import (
    LLMConfigurationError,
    LLMProvider,
    MissingCredentialError,
    UnsupportedProviderError,
)
from .config import LLMSettings
from .http import AsyncHTTPTransport
from .models import AgentDecision
from .providers import GeminiProvider, GroqProvider, MockLLMProvider


def _require_model(settings: LLMSettings) -> str:
    if settings.model is None or not settings.model.strip():
        raise LLMConfigurationError(
            f"LLM_MODEL is required for provider {settings.provider!r}."
        )
    return settings.model


def create_provider(
    settings: LLMSettings,
    *,
    transport: AsyncHTTPTransport | None = None,
    mock_decision: AgentDecision | None = None,
) -> LLMProvider:
    """Create only the explicitly selected provider."""

    provider = settings.provider.strip().lower()
    if provider == "mock":
        decision = mock_decision or AgentDecision(
            summary="No external provider was selected.",
            action="no_action",
            arguments={},
            expected_effect="Return a deterministic offline decision.",
            confidence=1.0,
        )
        return MockLLMProvider([decision], model=settings.model or "mock-model")
    if provider == "gemini":
        if settings.gemini_api_key is None:
            raise MissingCredentialError("GEMINI_API_KEY is required for Gemini.")
        return GeminiProvider(
            api_key=settings.gemini_api_key.get_secret_value(),
            model=_require_model(settings),
            transport=transport,
            retry_policy=settings.retry_policy,
        )
    if provider == "groq":
        if settings.groq_api_key is None:
            raise MissingCredentialError("GROQ_API_KEY is required for Groq.")
        return GroqProvider(
            api_key=settings.groq_api_key.get_secret_value(),
            model=_require_model(settings),
            transport=transport,
            retry_policy=settings.retry_policy,
        )
    raise UnsupportedProviderError(f"Unsupported LLM provider: {settings.provider!r}.")
