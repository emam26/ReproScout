from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence

import pytest
from pydantic import ValidationError

from reproscout.llm import (
    AgentDecision,
    GeminiProvider,
    GroqProvider,
    InvalidProviderResponseError,
    LLMConfigurationError,
    LLMRequest,
    LLMSettings,
    LLMUsage,
    MissingCredentialError,
    MockLLMProvider,
    ProviderAPIError,
    ProviderRateLimitError,
    RetryPolicy,
    UnsupportedProviderError,
    create_provider,
    get_llm_settings,
)
from reproscout.llm.http import HTTPResponse
from reproscout.state import SQLiteRunStore


class FakeTransport:
    def __init__(self, responses: Sequence[HTTPResponse | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    async def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout_seconds: float,
    ) -> HTTPResponse:
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "payload": payload,
                "timeout_seconds": timeout_seconds,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _decision(**overrides: object) -> AgentDecision:
    values: dict[str, object] = {
        "summary": "Inspect the documented test command.",
        "action": "inspect_file",
        "arguments": {"path": "README.md"},
        "expected_effect": "Identify the intended test invocation.",
        "confidence": 0.8,
    }
    values.update(overrides)
    return AgentDecision.model_validate(values)


def _request() -> LLMRequest:
    return LLMRequest(
        context={"stage": "ANALYZE", "error": "command not documented"},
        available_actions=["inspect_file", "search_repo"],
    )


def _gemini_response(
    decision: AgentDecision | None = None,
    *,
    text: str | None = None,
) -> HTTPResponse:
    content = text if text is not None else (decision or _decision()).model_dump_json()
    return HTTPResponse(
        200,
        json.dumps(
            {
                "candidates": [{"content": {"parts": [{"text": content}]}}],
                "usageMetadata": {
                    "promptTokenCount": 17,
                    "candidatesTokenCount": 11,
                },
            }
        ),
    )


def _groq_response(decision: AgentDecision | None = None) -> HTTPResponse:
    content = (decision or _decision()).model_dump_json()
    return HTTPResponse(
        200,
        json.dumps(
            {
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 13, "completion_tokens": 7},
            }
        ),
    )


def test_agent_decision_requires_strict_structured_output() -> None:
    with pytest.raises(ValidationError):
        AgentDecision.model_validate(
            {
                "summary": "Invalid action name.",
                "action": "Run Shell",
                "arguments": {},
                "expected_effect": "Nothing",
                "confidence": 1.2,
                "chain_of_thought": "must not be accepted",
            }
        )


def test_requests_and_decisions_reject_credential_fields() -> None:
    with pytest.raises(ValidationError, match="Sensitive field"):
        LLMRequest(
            context={"api_key": "do-not-persist"},
            available_actions=["inspect_file"],
        )
    with pytest.raises(ValidationError, match="Sensitive field"):
        _decision(arguments={"authorization": "Bearer secret"})


def test_state_persistence_rejects_credential_fields(tmp_path) -> None:
    with (
        SQLiteRunStore(tmp_path / "state.sqlite3") as store,
        pytest.raises(ValueError, match="Sensitive field"),
    ):
        store.create_run(context={"access_token": "do-not-store"})


def test_mock_provider_is_deterministic_and_offline() -> None:
    decision = _decision()
    provider = MockLLMProvider(
        [decision],
        usage=LLMUsage(input_tokens=4, output_tokens=3),
    )

    response = asyncio.run(provider.decide(_request()))

    assert response.decision == decision
    assert response.provider == "mock"
    assert response.usage.input_tokens == 4
    assert provider.call_count == 1


def test_environment_configuration_redacts_secrets() -> None:
    settings = get_llm_settings(
        {
            "LLM_PROVIDER": "GROQ",
            "LLM_MODEL": "configured-model",
            "GROQ_API_KEY": "super-secret-value",
        }
    )

    assert settings.provider == "groq"
    assert settings.model == "configured-model"
    assert "super-secret-value" not in repr(settings)
    assert "**********" in repr(settings)


def test_provider_selection_requires_explicit_supported_configuration() -> None:
    with pytest.raises(UnsupportedProviderError, match="unsupported"):
        create_provider(LLMSettings(provider="unsupported", model="model"))
    with pytest.raises(MissingCredentialError, match="GEMINI_API_KEY"):
        create_provider(LLMSettings(provider="gemini", model="model"))
    with pytest.raises(LLMConfigurationError, match="LLM_MODEL"):
        create_provider(LLMSettings(provider="groq", groq_api_key="configured-secret"))


def test_factory_never_fails_over_to_another_provider() -> None:
    provider = create_provider(
        LLMSettings(
            provider="gemini",
            model="configured-model",
            gemini_api_key="gemini-secret",
            groq_api_key="groq-secret",
        ),
        transport=FakeTransport([_gemini_response()]),
    )

    assert isinstance(provider, GeminiProvider)


def test_gemini_adapter_validates_decision_and_usage_metadata() -> None:
    transport = FakeTransport([_gemini_response()])
    provider = GeminiProvider(
        api_key="gemini-secret",
        model="configured-model",
        transport=transport,
        retry_policy=RetryPolicy(max_attempts=1),
    )

    response = asyncio.run(provider.decide(_request()))

    assert response.provider == "gemini"
    assert response.decision.action == "inspect_file"
    assert response.usage.input_tokens == 17
    assert response.usage.output_tokens == 11
    assert "gemini-secret" not in transport.calls[0]["url"]
    assert transport.calls[0]["headers"]["x-goog-api-key"] == "gemini-secret"


def test_groq_adapter_validates_decision_and_usage_metadata() -> None:
    transport = FakeTransport([_groq_response()])
    provider = GroqProvider(
        api_key="groq-secret",
        model="configured-model",
        transport=transport,
        retry_policy=RetryPolicy(max_attempts=1),
    )

    response = asyncio.run(provider.decide(_request()))

    assert response.provider == "groq"
    assert response.usage.input_tokens == 13
    assert response.usage.output_tokens == 7
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer groq-secret"


@pytest.mark.parametrize(
    "response",
    [
        HTTPResponse(200, ""),
        HTTPResponse(200, "not-json"),
        _gemini_response(text="not-json"),
        _gemini_response(text=json.dumps({"summary": "missing fields"})),
    ],
)
def test_gemini_rejects_empty_malformed_and_schema_invalid_output(
    response: HTTPResponse,
) -> None:
    provider = GeminiProvider(
        api_key="gemini-secret",
        model="configured-model",
        transport=FakeTransport([response]),
        retry_policy=RetryPolicy(max_attempts=1),
    )

    with pytest.raises(InvalidProviderResponseError):
        asyncio.run(provider.decide(_request()))


def test_provider_translates_rate_limit_without_leaking_body() -> None:
    transport = FakeTransport([HTTPResponse(429, "secret provider diagnostics")])
    provider = GroqProvider(
        api_key="groq-secret",
        model="configured-model",
        transport=transport,
        retry_policy=RetryPolicy(max_attempts=1),
    )

    with pytest.raises(ProviderRateLimitError) as exc_info:
        asyncio.run(provider.decide(_request()))

    assert "secret provider diagnostics" not in str(exc_info.value)


def test_retryable_errors_stop_at_configured_limit() -> None:
    transport = FakeTransport(
        [
            HTTPResponse(500, "first"),
            HTTPResponse(503, "second"),
            HTTPResponse(500, "third"),
        ]
    )
    provider = GeminiProvider(
        api_key="gemini-secret",
        model="configured-model",
        transport=transport,
        retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0),
    )

    with pytest.raises(ProviderAPIError, match="HTTP 500"):
        asyncio.run(provider.decide(_request()))

    assert len(transport.calls) == 3


def test_nonretryable_provider_error_is_not_retried() -> None:
    transport = FakeTransport([HTTPResponse(400, "bad request")])
    provider = GeminiProvider(
        api_key="gemini-secret",
        model="configured-model",
        transport=transport,
        retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0),
    )

    with pytest.raises(ProviderAPIError, match="HTTP 400"):
        asyncio.run(provider.decide(_request()))

    assert len(transport.calls) == 1


def test_provider_rejects_echoed_credentials() -> None:
    provider = GeminiProvider(
        api_key="gemini-secret",
        model="configured-model",
        transport=FakeTransport([HTTPResponse(200, '{"echo":"gemini-secret"}')]),
        retry_policy=RetryPolicy(max_attempts=1),
    )

    with pytest.raises(InvalidProviderResponseError, match="credential"):
        asyncio.run(provider.decide(_request()))
