"""Shared guards that keep credential-shaped data out of durable records."""

from __future__ import annotations

import re
import stat
from collections.abc import Mapping
from pathlib import Path, PurePosixPath, PureWindowsPath

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


class BoundedReadError(ValueError):
    """Raised when a repository-local read violates its safety bound."""


def read_bounded_workspace_file(
    root: Path,
    relative_path: str,
    *,
    max_bytes: int,
) -> bytes:
    """Read one regular, non-symlink file without loading unbounded data."""

    if max_bytes < 1 or not isinstance(relative_path, str) or not relative_path:
        raise BoundedReadError("Workspace read parameters are invalid.")
    normalized = relative_path.replace("\\", "/")
    posix = PurePosixPath(normalized)
    if (
        posix.is_absolute()
        or PureWindowsPath(relative_path).is_absolute()
        or any(part in {"", ".", ".."} for part in posix.parts)
    ):
        raise BoundedReadError("Workspace read path must remain relative.")
    root_path = Path(root).expanduser().resolve(strict=True)
    if not root_path.is_dir():
        raise BoundedReadError("Workspace read root must be a directory.")
    current = root_path
    for part in posix.parts:
        current /= part
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise BoundedReadError(
                "Workspace file metadata could not be read."
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise BoundedReadError("Symlinked workspace files are not readable.")
        if not stat.S_ISDIR(metadata.st_mode) and current != root_path / Path(
            *posix.parts
        ):
            raise BoundedReadError("Workspace path contains a non-directory component.")
    target = root_path.joinpath(*posix.parts)
    try:
        metadata = target.lstat()
    except OSError as exc:
        raise BoundedReadError("Workspace file metadata could not be read.") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise BoundedReadError("Workspace read target must be a regular file.")
    chunks: list[bytes] = []
    total = 0
    try:
        with target.open("rb") as stream:
            while total <= max_bytes:
                chunk = stream.read(min(64 * 1024, max_bytes - total + 1))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > max_bytes:
                    raise BoundedReadError("Workspace file exceeds the read limit.")
    except BoundedReadError:
        raise
    except OSError as exc:
        raise BoundedReadError("Workspace file could not be read safely.") from exc
    return b"".join(chunks)


def read_bounded_workspace_text(
    root: Path,
    relative_path: str,
    *,
    max_bytes: int,
) -> str:
    """Read bounded UTF-8-compatible repository text."""

    return read_bounded_workspace_file(
        root,
        relative_path,
        max_bytes=max_bytes,
    ).decode("utf-8", errors="replace")


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
