"""Policy checks for diagnosis proposals before Phase 9 repair execution."""

from __future__ import annotations

import re
from collections.abc import Mapping

from reproscout.diagnostics import DiagnosticContext

from .models import Diagnosis, DiagnosisLimits, DiagnosisRisk


class DiagnosisPolicyError(ValueError):
    """Raised when an otherwise well-formed diagnosis violates policy."""


class RepeatedDiagnosisError(DiagnosisPolicyError):
    """Raised when a valid recommendation has already repeated too often."""


_COMMAND_KEYS = {
    "command",
    "command_line",
    "executable",
    "script",
    "shell",
    "shell_command",
}
_SENSITIVE_NAME = re.compile(
    r"(?:key|token|secret|password|passwd|authorization|credential|private)",
    re.IGNORECASE,
)
_SHELL_SYNTAX = re.compile(r"(?:\r|\n|&&|\|\||[|;`])")
_DISALLOWED_ACTIONS = {"RUN_ANY_SHELL_AS_ROOT", "MODIFY_HOST", "MANAGE_DOCKER_GLOBALLY"}


def _walk_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [item for nested in value.values() for item in _walk_strings(nested)]
    if isinstance(value, list):
        return [item for nested in value for item in _walk_strings(nested)]
    return []


def validate_diagnosis(
    diagnosis: Diagnosis,
    context: DiagnosticContext,
    *,
    limits: DiagnosisLimits,
) -> None:
    """Reject unsupported actions, unknown evidence, secrets, and risky proposals."""

    if diagnosis.failure_class is not context.failure_class:
        raise DiagnosisPolicyError(
            "Diagnosis cannot replace the deterministic failure classification."
        )
    known_references = {item.reference for item in context.evidence}
    references = set(diagnosis.supporting_evidence)
    references.update(
        reference
        for hypothesis in diagnosis.hypotheses
        for reference in hypothesis.supporting_evidence
    )
    unknown = sorted(references - known_references)
    if unknown:
        raise DiagnosisPolicyError(
            f"Diagnosis references unavailable evidence: {', '.join(unknown)}."
        )
    if len(diagnosis.hypotheses) > limits.max_hypotheses:
        raise DiagnosisPolicyError("Diagnosis contains too many hypotheses.")
    if diagnosis.risk is DiagnosisRisk.HIGH:
        raise DiagnosisPolicyError(
            "High-risk diagnosis recommendations require review."
        )
    if diagnosis.recommended_action.value in _DISALLOWED_ACTIONS:
        raise DiagnosisPolicyError("Diagnosis requested a prohibited action.")
    for key, value in diagnosis.action_arguments.items():
        if key.lower() in _COMMAND_KEYS:
            raise DiagnosisPolicyError(
                "Diagnosis arguments cannot contain arbitrary shell commands."
            )
        if _SENSITIVE_NAME.search(key):
            raise DiagnosisPolicyError("Diagnosis arguments contain a sensitive name.")
        if any(_SHELL_SYNTAX.search(item) for item in _walk_strings(value)):
            raise DiagnosisPolicyError("Diagnosis arguments contain shell syntax.")
        if (
            key.lower() == "variable_name"
            and isinstance(value, str)
            and _SENSITIVE_NAME.search(value)
        ):
            raise DiagnosisPolicyError(
                "Diagnosis cannot recommend setting a sensitive environment variable."
            )
