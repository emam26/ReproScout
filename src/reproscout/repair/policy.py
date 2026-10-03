"""Deterministic policy validation for finite repair actions."""

from __future__ import annotations

import re

from packaging.requirements import InvalidRequirement, Requirement
from packaging.version import InvalidVersion, Version

from reproscout.diagnostics import DiagnosticContext
from reproscout.network import PublicUrlError, validate_public_url
from reproscout.planning.safety import PlanSafetyError, validate_command

from .models import (
    RepairAction,
    RepairActionType,
    RepairLimits,
    RepairRisk,
    Reversibility,
)


class RepairPolicyError(ValueError):
    """Raised when a repair proposal is unsafe or outside Phase 9 scope."""


_SAFE_VARIABLE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_SENSITIVE_VARIABLE = re.compile(
    r"(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|AUTH|CREDENTIAL|PRIVATE)",
    re.IGNORECASE,
)
_STEP_ID = re.compile(r"^step-[0-9]{3}$")
_SAFE_PATH = re.compile(r"^[^\x00\r\n]+$")
_FORBIDDEN_KEYS = {"shell", "shell_command", "script", "executable", "root"}


def _relative_path(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or not _SAFE_PATH.fullmatch(value):
        raise RepairPolicyError(f"{field} must be a non-empty safe path.")
    normalized = value.replace("\\", "/")
    if (
        normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or ".." in normalized.split("/")
    ):
        raise RepairPolicyError(f"{field} must remain inside the workspace.")
    return normalized


def _require_requirement(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RepairPolicyError(f"{field} must be a requirement string.")
    try:
        requirement = Requirement(value)
    except InvalidRequirement as exc:
        raise RepairPolicyError(f"{field} is not a valid PEP 508 requirement.") from exc
    if requirement.url is not None:
        raise RepairPolicyError(
            "Direct URL and VCS dependencies are not allowed in Phase 9."
        )
    return str(requirement)


def _validate_public_documented_url(value: object, context: DiagnosticContext) -> str:
    if not isinstance(value, str):
        raise RepairPolicyError("Asset URL must be a string.")
    try:
        validated = validate_public_url(value)
    except PublicUrlError as exc:
        raise RepairPolicyError(str(exc)) from exc
    if value not in "\n".join(item.content for item in context.evidence):
        raise RepairPolicyError("Asset URL must be present in trusted evidence.")
    return validated


def validate_repair_action(
    action: RepairAction,
    context: DiagnosticContext,
    *,
    limits: RepairLimits,
) -> None:
    """Validate evidence references, risk, typed arguments, and command safety."""

    known = {item.reference for item in context.evidence}
    unknown = sorted(set(action.supporting_evidence) - known)
    if unknown:
        raise RepairPolicyError(
            f"Repair references unavailable evidence: {', '.join(unknown)}."
        )
    if action.risk is RepairRisk.HIGH or (
        action.risk is RepairRisk.MEDIUM and limits.max_risk is RepairRisk.LOW
    ):
        raise RepairPolicyError("Repair risk exceeds the configured policy.")
    for key in action.arguments:
        if key.lower() in _FORBIDDEN_KEYS:
            raise RepairPolicyError(
                "Repair arguments contain an unrestricted execution field."
            )
        if _SENSITIVE_VARIABLE.search(key):
            raise RepairPolicyError("Repair arguments contain sensitive material.")
    arguments = action.arguments
    if action.action_type is RepairActionType.CHANGE_INVOCATION:
        step_id = arguments.get("step_id")
        command = arguments.get("command")
        if not isinstance(step_id, str) or not _STEP_ID.fullmatch(step_id):
            raise RepairPolicyError("Invocation repair requires a valid step_id.")
        if not isinstance(command, str):
            raise RepairPolicyError("Invocation repair requires a command.")
        try:
            validate_command(command, max_length=1_000)
        except PlanSafetyError as exc:
            raise RepairPolicyError(str(exc)) from exc
    elif action.action_type is RepairActionType.CHANGE_PYTHON_VERSION:
        version = arguments.get("python_version")
        if not isinstance(version, str):
            raise RepairPolicyError("Python-version repair requires python_version.")
        try:
            parsed = Version(version)
        except InvalidVersion as exc:
            raise RepairPolicyError("Python version is not valid PEP 440.") from exc
        if parsed.release[0] != 3 or len(parsed.release) < 2:
            raise RepairPolicyError("Only bounded Python 3.x candidates are allowed.")
    elif action.action_type in {
        RepairActionType.ADD_DEPENDENCY,
        RepairActionType.CHANGE_DEPENDENCY_VERSION,
    }:
        _require_requirement(arguments.get("requirement"), field="requirement")
    elif action.action_type is RepairActionType.SET_SAFE_ENVIRONMENT_VARIABLE:
        name = arguments.get("name")
        value = arguments.get("value")
        if (
            not isinstance(name, str)
            or not _SAFE_VARIABLE.fullmatch(name)
            or _SENSITIVE_VARIABLE.search(name)
        ):
            raise RepairPolicyError(
                "Only non-sensitive safe environment names are allowed."
            )
        if (
            not isinstance(value, str)
            or len(value) > 1_000
            or any(char in value for char in "\r\n\x00")
        ):
            raise RepairPolicyError(
                "Environment value is invalid or exceeds its bound."
            )
    elif action.action_type is RepairActionType.CREATE_REQUIRED_DIRECTORY:
        _relative_path(arguments.get("path"), "directory path")
    elif action.action_type is RepairActionType.ADJUST_CONFIG_PATH:
        _relative_path(arguments.get("path"), "config path")
    elif action.action_type is RepairActionType.FETCH_DOCUMENTED_ASSET:
        _validate_public_documented_url(arguments.get("url"), context)
        size = arguments.get("max_bytes", limits.max_download_bytes)
        if not isinstance(size, int) or size < 1 or size > limits.max_download_bytes:
            raise RepairPolicyError("Asset download size exceeds the configured bound.")
    elif action.action_type is RepairActionType.APPLY_MINIMAL_PATCH:
        patch = arguments.get("patch")
        if not isinstance(patch, str) or len(patch) > limits.max_patch_characters:
            raise RepairPolicyError("Patch is missing or exceeds the configured bound.")
        if "\x00" in patch or "--- /" in patch or "+++ /" in patch:
            raise RepairPolicyError("Patch contains unsafe absolute paths.")
    elif action.action_type in {
        RepairActionType.GATHER_MORE_EVIDENCE,
        RepairActionType.STOP_UNREPAIRABLE,
    }:
        if arguments:
            raise RepairPolicyError("Control actions cannot carry execution arguments.")
    if (
        action.action_type is RepairActionType.APPLY_MINIMAL_PATCH
        and action.reversibility is not Reversibility.ROLLBACK_REQUIRED
    ):
        raise RepairPolicyError("Minimal patches require rollback metadata.")
