"""Provider interface, retry policy, and translated failure types."""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from .models import AgentDecision, LLMRequest, LLMResponse, LLMUsage


class LLMError(RuntimeError):
    """Base error for provider-independent LLM failures."""

    retryable = False


class LLMConfigurationError(LLMError):
    """Raised when provider configuration is incomplete or invalid."""


class MissingCredentialError(LLMConfigurationError):
    """Raised when the selected provider has no configured API key."""


class UnsupportedProviderError(LLMConfigurationError):
    """Raised when an unknown provider name is selected."""


class ProviderTimeoutError(LLMError):
    """Raised when the provider exceeds the configured timeout."""

    retryable = True


class ProviderRateLimitError(LLMError):
    """Raised for provider rate limiting."""

    retryable = True


class ProviderNetworkError(LLMError):
    """Raised for transient transport failures."""

    retryable = True


class ProviderAPIError(LLMError):
    """Raised when a provider returns an unsuccessful HTTP status."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        self.retryable = retryable
        super().__init__(message)


class InvalidProviderResponseError(LLMError):
    """Raised when provider output is empty, malformed, or schema-invalid."""


class RetryPolicy(BaseModel):
    """Finite retry configuration for genuinely transient failures."""

    model_config = ConfigDict(frozen=True)

    max_attempts: Annotated[int, Field(ge=1, le=5)] = 3
    base_delay_seconds: Annotated[float, Field(ge=0, le=10)] = 0.25


class LLMProvider(ABC):
    """Provider-independent asynchronous decision interface."""

    @abstractmethod
    async def decide(self, request: LLMRequest) -> LLMResponse:
        """Return a validated structured decision."""


class RetryingLLMProvider(LLMProvider):
    """Shared bounded retry behavior for network-backed adapters."""

    provider_name: str

    def __init__(self, model: str, retry_policy: RetryPolicy | None = None) -> None:
        if not model.strip():
            raise LLMConfigurationError("LLM model ID cannot be empty.")
        self.model = model.strip()
        self.retry_policy = retry_policy or RetryPolicy()

    async def decide(self, request: LLMRequest) -> LLMResponse:
        started = time.monotonic()
        for attempt_number in range(1, self.retry_policy.max_attempts + 1):
            try:
                decision, usage = await self._request_decision(request)
                return LLMResponse(
                    decision=decision,
                    provider=self.provider_name,
                    model=self.model,
                    usage=usage,
                    latency_seconds=time.monotonic() - started,
                )
            except LLMError as exc:
                if (
                    not exc.retryable
                    or attempt_number >= self.retry_policy.max_attempts
                ):
                    raise
                delay = self.retry_policy.base_delay_seconds * (
                    2 ** (attempt_number - 1)
                )
                if delay:
                    await asyncio.sleep(delay)
        raise AssertionError("Retry loop terminated unexpectedly.")

    @abstractmethod
    async def _request_decision(
        self,
        request: LLMRequest,
    ) -> tuple[AgentDecision, LLMUsage]:
        """Perform one provider request without retrying."""
