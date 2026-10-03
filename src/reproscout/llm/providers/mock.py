"""Deterministic offline provider for unit tests and local development."""

from __future__ import annotations

import time
from collections.abc import Sequence

from ..base import InvalidProviderResponseError, LLMError, LLMProvider
from ..models import AgentDecision, LLMRequest, LLMResponse, LLMUsage


class MockLLMProvider(LLMProvider):
    """Return scripted validated decisions without network access."""

    def __init__(
        self,
        responses: Sequence[AgentDecision | LLMError],
        *,
        model: str = "mock-model",
        usage: LLMUsage | None = None,
    ) -> None:
        self._responses = list(responses)
        self.model = model
        self.usage = usage or LLMUsage(input_tokens=0, output_tokens=0)
        self.call_count = 0

    async def decide(self, request: LLMRequest) -> LLMResponse:
        del request
        started = time.monotonic()
        if self.call_count >= len(self._responses):
            raise InvalidProviderResponseError("Mock provider responses are exhausted.")
        response = self._responses[self.call_count]
        self.call_count += 1
        if isinstance(response, LLMError):
            raise response
        return LLMResponse(
            decision=response,
            provider="mock",
            model=self.model,
            usage=self.usage,
            latency_seconds=time.monotonic() - started,
        )
