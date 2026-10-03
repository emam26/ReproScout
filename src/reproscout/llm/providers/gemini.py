"""Gemini REST adapter using provider-native JSON-schema output."""

from __future__ import annotations

from urllib.parse import quote

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


class GeminiProvider(RetryingLLMProvider):
    """Google Gemini adapter with strict application-side validation."""

    provider_name = "gemini"
    _BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        transport: AsyncHTTPTransport | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Gemini API key cannot be empty.")
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
            "systemInstruction": {"parts": [{"text": request.system_instruction}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": request.max_output_tokens,
                "responseMimeType": "application/json",
                "responseJsonSchema": AgentDecision.model_json_schema(),
            },
        }
        response = await self._transport.post_json(
            f"{self._BASE_URL}/{quote(self.model, safe='')}:generateContent",
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self._api_key,
            },
            payload=payload,
            timeout_seconds=request.timeout_seconds,
        )
        require_success(response.status_code, "Gemini")
        if response_contains_secret(response.body, self._api_key):
            raise InvalidProviderResponseError(
                "Gemini response contained credential material."
            )
        envelope = parse_json_object(response.body, "Gemini")
        try:
            decision_text = envelope["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise InvalidProviderResponseError(
                "Gemini response did not contain a structured decision."
            ) from exc
        usage_value = envelope.get("usageMetadata")
        usage_data = usage_value if isinstance(usage_value, dict) else {}
        return (
            parse_decision_json(decision_text, "Gemini"),
            LLMUsage(
                input_tokens=optional_nonnegative_int(
                    usage_data.get("promptTokenCount")
                ),
                output_tokens=optional_nonnegative_int(
                    usage_data.get("candidatesTokenCount")
                ),
            ),
        )
