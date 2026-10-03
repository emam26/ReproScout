"""Shared guards that keep credential-shaped data out of durable records."""

from __future__ import annotations

import re
from collections.abc import Mapping

_SECRET_NAME = (
    r"(?:[a-z0-9]+_)*(?:api_?key|access_?token|auth_?token|refresh_?token|token|"
    r"authorization|password|passwd|client_?secret|secret|private_?key|ssh_?key|"
    r"credentials?)"
)
_SENSITIVE_KEY = re.compile(
    rf"^{_SECRET_NAME}$",
    re.IGNORECASE,
)
_SENSITIVE_ASSIGNMENT = re.compile(
    rf"(?i)(?P<name>{_SECRET_NAME})"
    r"(?P<separator>\s*[=:]\s*)"
    r"(?P<value>\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s]+)"
)
_AUTHORIZATION_HEADER = re.compile(r"(?im)\bauthorization\s*:\s*[^\r\n]+")
_BEARER_TOKEN = re.compile(r"(?i)\bBearer\s+[^\s]+")
_CREDENTIAL_URL = re.compile(r"(?i)(?P<scheme>\b[a-z][a-z0-9+.-]*://)[^/\s@]+@")


def reject_sensitive_mapping(value: Mapping[str, object]) -> None:
    """Reject nested mappings whose keys indicate credential material."""

    for key, item in value.items():
        normalized = key.replace("-", "_").strip()
        if _SENSITIVE_KEY.fullmatch(normalized):
            raise ValueError(f"Sensitive field {key!r} is not allowed.")
        _reject_sensitive_value(item)


def _reject_sensitive_value(value: object) -> None:
    if isinstance(value, Mapping):
        reject_sensitive_mapping(value)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _reject_sensitive_value(nested)


def response_contains_secret(response_text: str, secret: str) -> bool:
    """Return whether a provider echoed its credential in response content."""

    return bool(secret) and secret in response_text


def redact_sensitive_text(value: str) -> str:
    """Redact common inline credential representations from bounded evidence."""

    redacted = _AUTHORIZATION_HEADER.sub("Authorization: <redacted>", value)
    redacted = _SENSITIVE_ASSIGNMENT.sub(
        r"\g<name>\g<separator><redacted>",
        redacted,
    )
    redacted = _BEARER_TOKEN.sub("Bearer <redacted>", redacted)
    return _CREDENTIAL_URL.sub(r"\g<scheme><redacted>@", redacted)
