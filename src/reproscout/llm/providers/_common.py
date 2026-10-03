"""Shared parsing and error translation for vendor adapters."""

from __future__ import annotations

import json

from pydantic import ValidationError

from ..base import (
    InvalidProviderResponseError,
    ProviderAPIError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from ..models import AgentDecision


def require_success(status_code: int, provider: str) -> None:
    """Translate HTTP status codes without including provider response bodies."""

    if 200 <= status_code < 300:
        return
    if status_code == 429:
        raise ProviderRateLimitError(f"{provider} rate limit exceeded.")
    if status_code in {408, 504}:
        raise ProviderTimeoutError(f"{provider} request timed out.")
    raise ProviderAPIError(
        f"{provider} API returned HTTP {status_code}.",
        retryable=status_code >= 500,
    )


def parse_json_object(body: str, provider: str) -> dict[str, object]:
    """Parse one non-empty provider envelope as a JSON object."""

    if not body.strip():
        raise InvalidProviderResponseError(f"{provider} returned an empty response.")
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise InvalidProviderResponseError(
            f"{provider} returned malformed JSON."
        ) from exc
    if not isinstance(parsed, dict):
        raise InvalidProviderResponseError(
            f"{provider} response must be a JSON object."
        )
    return parsed


def parse_decision_json(value: object, provider: str) -> AgentDecision:
    """Validate a provider's structured decision with the application schema."""

    if not isinstance(value, str) or not value.strip():
        raise InvalidProviderResponseError(
            f"{provider} returned no structured decision."
        )
    try:
        return AgentDecision.model_validate_json(value)
    except (ValidationError, ValueError) as exc:
        raise InvalidProviderResponseError(
            f"{provider} decision failed schema validation."
        ) from exc


def optional_nonnegative_int(value: object) -> int | None:
    """Return provider usage only when represented by a valid integer."""

    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None
