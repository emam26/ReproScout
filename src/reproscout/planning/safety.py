"""Deterministic command and working-directory safety checks."""

from __future__ import annotations

import re
from pathlib import PurePosixPath, PureWindowsPath


class PlanSafetyError(ValueError):
    """Raised when a proposed plan contains a prohibited operation."""


_PROHIBITED_COMMANDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(?:[a-z0-9]+_)*(?:api_?key|access_?token|auth_?token|"
            r"refresh_?token|token|password|passwd|client_?secret|secret|"
            r"authorization|private_?key|ssh_?key|credentials?)"
            r"\s*[=:]",
            re.IGNORECASE,
        ),
        "inline credential material",
    ),
    (
        re.compile(r"\bBearer\s+[^\s]+", re.IGNORECASE),
        "inline bearer credentials",
    ),
    (
        re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s@]+@", re.IGNORECASE),
        "URL credentials",
    ),
    (re.compile(r"(?:^|\s)(?:sudo|su)(?:\s|$)", re.IGNORECASE), "privilege escalation"),
    (re.compile(r"--privileged\b", re.IGNORECASE), "privileged containers"),
    (
        re.compile(r"(?:/var/run/docker\.sock|docker\.sock)", re.IGNORECASE),
        "Docker socket access",
    ),
    (
        re.compile(
            r"\bdocker\s+(?:system|container|image|volume)\s+prune\b",
            re.IGNORECASE,
        ),
        "global Docker cleanup",
    ),
    (re.compile(r"\bdocker\s+(?:rm|stop)\b", re.IGNORECASE), "container management"),
    (
        re.compile(
            r"\brm\s+-[^\n]*r[^\n]*f[^\n]*(?:/|~|\$HOME)(?:\s|$)",
            re.IGNORECASE,
        ),
        "broad filesystem deletion",
    ),
    (
        re.compile(r"\b(?:mkfs|shutdown|reboot)\b", re.IGNORECASE),
        "host-destructive command",
    ),
    (
        re.compile(
            r"\b(?:apt|apt-get|dnf|yum|pacman|brew|choco|winget)\s+"
            r"(?:install|remove|upgrade|update)\b",
            re.IGNORECASE,
        ),
        "system package-manager modification",
    ),
    (
        re.compile(
            r"(?:^|\s)(?:/etc/|/usr/|/var/|[A-Za-z]:\\Windows\\)", re.IGNORECASE
        ),
        "host system path access",
    ),
    (
        re.compile(
            r"(?:^|\s)(?:~?/\.ssh|~?/\.aws|~?/\.gcloud)(?:/|\s|$)", re.IGNORECASE
        ),
        "credential path access",
    ),
)


def validate_command(command: str, *, max_length: int) -> None:
    """Reject unsafe, multiline, or excessive commands before execution exists."""

    if not command.strip():
        raise PlanSafetyError("Plan command cannot be empty.")
    if len(command) > max_length:
        raise PlanSafetyError(f"Plan command exceeds the {max_length}-character limit.")
    if "\n" in command or "\r" in command:
        raise PlanSafetyError("Multiline plan commands are not allowed.")
    for pattern, reason in _PROHIBITED_COMMANDS:
        if pattern.search(command):
            raise PlanSafetyError(f"Plan command requests prohibited {reason}.")


def validate_working_directory(value: str) -> None:
    """Require a relative path contained by the future sandbox workspace."""

    posix = PurePosixPath(value.replace("\\", "/"))
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or ".." in posix.parts:
        raise PlanSafetyError(
            "Plan working directory must stay within the repository workspace."
        )
