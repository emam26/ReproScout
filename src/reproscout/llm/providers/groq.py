"""Groq REST adapter using provider-native JSON-schema output."""

from __future__ import annotations

from reproscout.security import response_contains_secret

from ..base import InvalidProviderResponseError, RetryingLLMProvider, RetryPolicy
from ..http import AsyncHTTPTransport, UrllibHTTPTransport
from ..models import AgentDecision, LLMRequest, LLMUsage
from ._common import (
    optional_nonnegative_int,
    parse_decision_json,
    parse_json_object,
    require_success,
)


class GroqProvider(RetryingLLMProvider):
    """Groq adapter with explicit selection and no hidden provider failover."""

    provider_name = "groq"
    _URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        transport: AsyncHTTPTransport | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Groq API key cannot be empty.")
        super().__init__(model, retry_policy)
        self._api_key = api_key
        self._transport = transport or UrllibHTTPTransport()

    async def _request_decision(
        self,
        request: LLMRequest,
    ) -> tuple[AgentDecision, LLMUsage]:
        prompt = request.model_dump_json(
            exclude={"system_instruction", "max_output_tokens", "timeout_seconds"}
        )
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system_instruction},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": request.max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "agent_decision",
                    "strict": True,
                    "schema": AgentDecision.model_json_schema(),
                },
            },
        }
        response = await self._transport.post_json(
            self._URL,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            payload=payload,
            timeout_seconds=request.timeout_seconds,
        )
        require_success(response.status_code, "Groq")
        if response_contains_secret(response.body, self._api_key):
            raise InvalidProviderResponseError(
                "Groq response contained credential material."
            )
        envelope = parse_json_object(response.body, "Groq")
        try:
            decision_text = envelope["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise InvalidProviderResponseError(
                "Groq response did not contain a structured decision."
            ) from exc
        usage_value = envelope.get("usage")
        usage_data = usage_value if isinstance(usage_value, dict) else {}
        return (
            parse_decision_json(decision_text, "Groq"),
            LLMUsage(
                input_tokens=optional_nonnegative_int(usage_data.get("prompt_tokens")),
                output_tokens=optional_nonnegative_int(
                    usage_data.get("completion_tokens")
                ),
            ),
        )
