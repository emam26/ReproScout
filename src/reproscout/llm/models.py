"""Typed requests, decisions, responses, and usage metadata for LLM providers."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from reproscout.security import reject_sensitive_mapping


class StrictModel(BaseModel):
    """Immutable model that rejects unrecognized provider output fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentDecision(StrictModel):
    """Concise, auditable action proposal without hidden reasoning traces."""

    summary: Annotated[str, Field(min_length=1, max_length=2_000)]
    action: Annotated[
        str,
        Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]*$"),
    ]
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    expected_effect: Annotated[str, Field(min_length=1, max_length=2_000)]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]

    @field_validator("arguments")
    @classmethod
    def _arguments_must_not_contain_secrets(
        cls,
        value: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        reject_sensitive_mapping(value)
        return value


class LLMRequest(StrictModel):
    """Provider-independent structured reasoning request."""

    context: dict[str, JsonValue]
    available_actions: Annotated[list[str], Field(min_length=1, max_length=50)]
    system_instruction: Annotated[
        str,
        Field(min_length=1, max_length=4_000),
    ] = (
        "Choose one available action. Return only a concise structured decision; "
        "do not provide private chain-of-thought."
    )
    max_output_tokens: Annotated[int, Field(ge=64, le=8_192)] = 1_024
    timeout_seconds: Annotated[float, Field(gt=0, le=300)] = 30.0

    @field_validator("context")
    @classmethod
    def _context_must_not_contain_secrets(
        cls,
        value: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        reject_sensitive_mapping(value)
        return value

    @field_validator("available_actions")
    @classmethod
    def _actions_must_be_unique(cls, value: list[str]) -> list[str]:
        if any(not action.strip() for action in value):
            raise ValueError("Available action names cannot be empty.")
        if len(set(value)) != len(value):
            raise ValueError("Available action names must be unique.")
        return value


class LLMUsage(StrictModel):
    """Optional provider token accounting."""

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class LLMResponse(StrictModel):
    """Validated decision plus non-secret provider metadata."""

    decision: AgentDecision
    provider: Annotated[str, Field(min_length=1, max_length=50)]
    model: Annotated[str, Field(min_length=1, max_length=200)]
    usage: LLMUsage = Field(default_factory=LLMUsage)
    latency_seconds: float = Field(ge=0)
