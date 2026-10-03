"""Environment-backed, secret-safe LLM provider configuration."""

from __future__ import annotations

import os
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .base import RetryPolicy


class LLMSettings(BaseModel):
    """Explicit provider selection and credentials loaded from the environment."""

    model_config = ConfigDict(frozen=True)

    provider: str = Field(default="mock", min_length=1)
    model: str | None = None
    gemini_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)


def get_llm_settings(environ: Mapping[str, str] | None = None) -> LLMSettings:
    """Load LLM settings without exposing credential values."""

    source = os.environ if environ is None else environ
    model = source.get("LLM_MODEL", "").strip() or None
    gemini_key = source.get("GEMINI_API_KEY", "").strip() or None
    groq_key = source.get("GROQ_API_KEY", "").strip() or None
    return LLMSettings(
        provider=source.get("LLM_PROVIDER", "mock").strip().lower() or "mock",
        model=model,
        gemini_api_key=gemini_key,
        groq_api_key=groq_key,
    )
