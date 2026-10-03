"""Deterministic verification over execution results and workspace facts."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import NamedTuple

from reproscout.diagnostics import EnvironmentFingerprint, VerificationContract
from reproscout.diagnostics.models import VerificationTarget, VerificationTargetType
from reproscout.execution import ExecutionRunResult, StepExecutionResult
from reproscout.security import redact_sensitive_text

from .models import (
    VerificationCheck,
    VerificationCheckStatus,
    VerificationEvidence,
    VerificationLevel,
    VerificationResult,
    VerificationResultStatus,
)


class VerificationEngineError(ValueError):
    """Raised when a verification request is unsafe or malformed."""


class VerificationLimits(NamedTuple):
    max_output_characters: int = 2_000
    max_hash_bytes: int = 100_000_000


class ObjectiveVerificationEngine:
    """Evaluate a finite contract without executing target commands."""

    def __init__(self, *, limits: VerificationLimits | None = None) -> None:
        self.limits = limits or VerificationLimits()
        if self.limits.max_output_characters < 100 or self.limits.max_hash_bytes < 1:
            raise ValueError("Verification limits are too small.")

    def verify(
        self,
        contract: VerificationContract,
        *,
        execution: ExecutionRunResult | None = None,
        workspace: Path | None = None,
        environment: EnvironmentFingerprint | None = None,
    ) -> VerificationResult:
        """Return objective check evidence and no final reproducibility status."""

        executable_steps = (
            [step for step in execution.steps if step.command is not None]
            if execution is not None
            else []
        )
        checks: list[VerificationCheck] = []
        for index, target in enumerate(contract.targets):
            checks.append(
                self._check_target(
                    target,
                    executable_steps,
                    execution=execution,
                    workspace=workspace,
                    environment=environment,
                    check_id=f"check-{index + 1:03d}",
                    step_index=index,
                )
            )
        required = [check for check in checks if check.required]
        if not required:
            status = VerificationResultStatus.UNSPECIFIED
        elif any(
            check.status is VerificationCheckStatus.EXECUTION_FAILED
            for check in required
        ):
            status = VerificationResultStatus.EXECUTION_FAILED
        elif any(
            check.status is VerificationCheckStatus.UNAVAILABLE for check in required
        ):
            status = VerificationResultStatus.UNAVAILABLE
        elif any(check.status is VerificationCheckStatus.FAILED for check in required):
            status = VerificationResultStatus.FAILED
        else:
            status = VerificationResultStatus.PASSED
        level = max((check.level for check in checks), key=self._level_order)
        return VerificationResult(
            status=status,
            level=level,
            contract_goal=contract.goal,
            checks=checks,
            summary=self._summary(status, checks),
            execution_run_id=execution.run_id if execution is not None else None,
        )

    def _check_target(
        self,
        target: VerificationTarget,
        steps: list[StepExecutionResult],
        *,
        execution: ExecutionRunResult | None,
        workspace: Path | None,
        environment: EnvironmentFingerprint | None,
        check_id: str,
        step_index: int,
    ) -> VerificationCheck:
        level = self._target_level(target.target_type)
        if target.target_type is VerificationTargetType.ENVIRONMENT_SETUP:
            if environment is None:
                return self._check(
                    check_id,
                    target,
                    level,
                    VerificationCheckStatus.UNAVAILABLE,
                    "Environment fingerprint evidence is unavailable.",
                    "environment",
                )
            return self._check(
                check_id,
                target,
                level,
                VerificationCheckStatus.PASSED,
                "Environment fingerprint is present.",
                f"python={environment.python_version};network={environment.network_mode}",
            )
        if target.target_type in {
            VerificationTargetType.INSTALLATION_SUCCEEDS,
            VerificationTargetType.COMMAND_EXITS_SUCCESSFULLY,
            VerificationTargetType.TESTS_EXECUTE,
            VerificationTargetType.OUTPUT_CONDITION,
        }:
            step = self._find_step(target, steps, step_index)
            if step is None:
                return self._check(
                    check_id,
                    target,
                    level,
                    VerificationCheckStatus.UNAVAILABLE,
                    "No recorded execution evidence matches this target.",
                    "execution",
                )
            if step.failure_kind is not None or step.exit_code != 0 or step.timed_out:
                return self._check(
                    check_id,
                    target,
                    level,
                    VerificationCheckStatus.EXECUTION_FAILED,
                    "The recorded target execution failed.",
                    self._step_detail(step),
                )
            if target.target_type is VerificationTargetType.OUTPUT_CONDITION:
                assert target.output_pattern is not None
                try:
                    matched = re.search(
                        target.output_pattern,
                        f"{step.stdout}\n{step.stderr}",
                    )
                except re.error:
                    matched = None
                if matched is None:
                    return self._check(
                        check_id,
                        target,
                        level,
                        VerificationCheckStatus.FAILED,
                        "The expected output condition was not observed.",
                        self._step_detail(step),
                    )
            return self._check(
                check_id,
                target,
                level,
                VerificationCheckStatus.PASSED,
                "Recorded execution evidence satisfies the target.",
                self._step_detail(step),
            )
        if target.target_type is VerificationTargetType.ARTIFACT_EXISTS:
            return self._check_artifact(
                check_id,
                target,
                workspace,
                level,
            )
        return self._check(
            check_id,
            target,
            level,
            VerificationCheckStatus.UNAVAILABLE,
            "Verification target type is not supported by this engine.",
            "verifier",
        )

    def _check_artifact(
        self,
        check_id: str,
        target: VerificationTarget,
        workspace: Path | None,
        level: VerificationLevel,
    ) -> VerificationCheck:
        if workspace is None:
            return self._check(
                check_id,
                target,
                level,
                VerificationCheckStatus.UNAVAILABLE,
                "Workspace evidence is unavailable.",
                "workspace",
            )
        try:
            path = self._safe_artifact_path(workspace, target.artifact_path or "")
        except VerificationEngineError as exc:
            return self._check(
                check_id,
                target,
                level,
                VerificationCheckStatus.UNAVAILABLE,
                str(exc),
                "workspace",
            )
        if not path.exists() and not path.is_symlink():
            return self._check(
                check_id,
                target,
                level,
                VerificationCheckStatus.FAILED,
                "Expected artifact does not exist.",
                str(target.artifact_path),
            )
        if target.artifact_type == "file" and not path.is_file():
            return self._artifact_failed(
                check_id, target, level, "Artifact is not a file."
            )
        if target.artifact_type == "directory" and not path.is_dir():
            return self._artifact_failed(
                check_id, target, level, "Artifact is not a directory."
            )
        if target.artifact_type == "symlink" and not path.is_symlink():
            return self._artifact_failed(
                check_id, target, level, "Artifact is not a symlink."
            )
        try:
            size = path.stat().st_size
        except OSError as exc:
            return self._check(
                check_id,
                target,
                level,
                VerificationCheckStatus.UNAVAILABLE,
                "Artifact metadata could not be read.",
                str(exc),
            )
        if (
            target.artifact_size_bytes is not None
            and size != target.artifact_size_bytes
        ):
            return self._artifact_failed(
                check_id, target, level, "Artifact size differs."
            )
        if target.artifact_sha256 is not None:
            if (
                not path.is_file()
                or path.is_symlink()
                or size > self.limits.max_hash_bytes
            ):
                return self._artifact_failed(
                    check_id,
                    target,
                    level,
                    "Artifact cannot be hashed within the verifier bounds.",
                )
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != target.artifact_sha256:
                return self._artifact_failed(
                    check_id, target, level, "Artifact hash differs."
                )
        return self._check(
            check_id,
            target,
            level,
            VerificationCheckStatus.PASSED,
            "Artifact exists and satisfies configured file facts.",
            f"path={target.artifact_path};size={size}",
        )

    def _artifact_failed(
        self,
        check_id: str,
        target: VerificationTarget,
        level: VerificationLevel,
        reason: str,
    ) -> VerificationCheck:
        return self._check(
            check_id,
            target,
            level,
            VerificationCheckStatus.FAILED,
            reason,
            str(target.artifact_path),
        )

    def _check(
        self,
        check_id: str,
        target: VerificationTarget,
        level: VerificationLevel,
        status: VerificationCheckStatus,
        reason: str,
        detail: str,
    ) -> VerificationCheck:
        return VerificationCheck(
            check_id=check_id,
            target_id=target.target_id,
            target_type=target.target_type,
            level=level,
            required=target.required,
            status=status,
            reason=reason,
            evidence=[
                VerificationEvidence(
                    evidence_id="evidence-001",
                    source=target.target_id,
                    detail=redact_sensitive_text(detail)[
                        : self.limits.max_output_characters
                    ],
                )
            ],
        )

    @staticmethod
    def _find_step(
        target: VerificationTarget,
        steps: list[StepExecutionResult],
        index: int,
    ) -> StepExecutionResult | None:
        if target.command is not None:
            for step in steps:
                if step.command == target.command:
                    return step
        return steps[index] if index < len(steps) else None

    @staticmethod
    def _step_detail(step: StepExecutionResult) -> str:
        output = f"exit_code={step.exit_code};timeout={step.timed_out};stdout={step.stdout};stderr={step.stderr}"
        return output[:2_000]

    @staticmethod
    def _safe_artifact_path(workspace: Path, relative: str) -> Path:
        if not relative or "\x00" in relative:
            raise VerificationEngineError("Artifact path is empty or invalid.")
        posix = PurePosixPath(relative.replace("\\", "/"))
        if (
            posix.is_absolute()
            or PureWindowsPath(relative).is_absolute()
            or any(part in {"", ".", ".."} for part in posix.parts)
        ):
            raise VerificationEngineError(
                "Artifact path must remain inside the workspace."
            )
        root = Path(workspace).resolve(strict=True)
        target = root.joinpath(*posix.parts)
        resolved = target.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise VerificationEngineError(
                "Artifact path escapes the workspace."
            ) from exc
        return target

    @staticmethod
    def _target_level(target_type: VerificationTargetType) -> VerificationLevel:
        if target_type is VerificationTargetType.ENVIRONMENT_SETUP:
            return VerificationLevel.L1
        if target_type is VerificationTargetType.TESTS_EXECUTE:
            return VerificationLevel.L3
        return VerificationLevel.L2

    @staticmethod
    def _level_order(level: VerificationLevel) -> int:
        return int(level.value[1])

    @staticmethod
    def _summary(
        status: VerificationResultStatus,
        checks: list[VerificationCheck],
    ) -> str:
        passed = sum(check.status is VerificationCheckStatus.PASSED for check in checks)
        return f"{status.value}: {passed}/{len(checks)} objective checks passed."
