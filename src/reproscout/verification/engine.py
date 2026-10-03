"""Deterministic verification over execution results and workspace facts."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import NamedTuple

from reproscout.diagnostics import EnvironmentFingerprint, VerificationContract
from reproscout.diagnostics.models import VerificationTarget, VerificationTargetType
from reproscout.execution import ExecutionRunResult, StepExecutionResult
from reproscout.security import (
    BoundedReadError,
    read_bounded_workspace_file,
    redact_sensitive_text,
)

from .models import (
    GoalCoverage,
    GoalMilestoneObservation,
    GoalMilestoneStatus,
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
        identity_error = self._identity_error(contract, execution)
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
                    identity_error=identity_error,
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
        level = max(
            (
                check.level
                for check in checks
                if check.status is VerificationCheckStatus.PASSED
            ),
            key=self._level_order,
            default=VerificationLevel.L0,
        )
        coverage = self._goal_coverage(contract, checks)
        return VerificationResult(
            status=status,
            level=level,
            contract_goal=contract.goal,
            checks=checks,
            summary=self._summary(status, checks),
            execution_run_id=execution.run_id if execution is not None else None,
            goal_coverage=coverage,
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
        identity_error: str | None,
    ) -> VerificationCheck:
        level = self._target_level(target.target_type)
        if identity_error is not None:
            return self._check(
                check_id,
                target,
                level,
                VerificationCheckStatus.UNAVAILABLE,
                identity_error,
                "verification-identity",
                milestone_status=GoalMilestoneStatus.NOT_EXECUTED,
            )
        if target.target_type is VerificationTargetType.GOAL_MILESTONE:
            return self._check(
                check_id,
                target,
                level,
                VerificationCheckStatus.UNAVAILABLE,
                "Required goal milestone has no executable command evidence.",
                "goal-coverage",
                milestone_status=GoalMilestoneStatus.NOT_EXECUTED,
            )
        if target.target_type is VerificationTargetType.ENVIRONMENT_SETUP:
            if environment is None:
                return self._check(
                    check_id,
                    target,
                    level,
                    VerificationCheckStatus.UNAVAILABLE,
                    "Environment fingerprint evidence is unavailable.",
                    "environment",
                    milestone_status=GoalMilestoneStatus.NOT_EXECUTED,
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
                    milestone_status=GoalMilestoneStatus.NOT_EXECUTED,
                )
            combined = f"{step.stdout}\n{step.stderr}".lower()
            if target.target_type is VerificationTargetType.TESTS_EXECUTE and (
                "no module named pytest" in combined
                or "pytest: command not found" in combined
            ):
                return self._check(
                    check_id,
                    target,
                    level,
                    VerificationCheckStatus.UNAVAILABLE,
                    "The test tool was unavailable in the resolved environment.",
                    self._step_detail(step),
                    milestone_status=GoalMilestoneStatus.TOOL_UNAVAILABLE,
                )
            if step.failure_kind is not None or step.exit_code != 0 or step.timed_out:
                return self._check(
                    check_id,
                    target,
                    level,
                    VerificationCheckStatus.EXECUTION_FAILED,
                    "The recorded target execution failed.",
                    self._step_detail(step),
                    milestone_status=GoalMilestoneStatus.FAILED,
                )
            if target.target_type is VerificationTargetType.TESTS_EXECUTE:
                test_status, test_reason = self._test_observation(combined)
                if test_status is not GoalMilestoneStatus.PASSED:
                    return self._check(
                        check_id,
                        target,
                        level,
                        VerificationCheckStatus.FAILED,
                        test_reason,
                        self._step_detail(step),
                        milestone_status=test_status,
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
                        milestone_status=GoalMilestoneStatus.FAILED,
                    )
            return self._check(
                check_id,
                target,
                level,
                VerificationCheckStatus.PASSED,
                "Recorded execution evidence satisfies the target.",
                self._step_detail(step),
                milestone_status=GoalMilestoneStatus.PASSED,
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
                milestone_status=GoalMilestoneStatus.FAILED,
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
            try:
                digest = hashlib.sha256(
                    read_bounded_workspace_file(
                        workspace,
                        target.artifact_path or "",
                        max_bytes=self.limits.max_hash_bytes,
                    )
                ).hexdigest()
            except BoundedReadError:
                return self._artifact_failed(
                    check_id,
                    target,
                    level,
                    "Artifact could not be read within verifier bounds.",
                )
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
        *,
        milestone_status: GoalMilestoneStatus | None = None,
    ) -> VerificationCheck:
        return VerificationCheck(
            check_id=check_id,
            target_id=target.target_id,
            step_id=target.step_id,
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
            milestone=target.milestone,
            milestone_status=milestone_status,
        )

    @staticmethod
    def _find_step(
        target: VerificationTarget,
        steps: list[StepExecutionResult],
        index: int,
    ) -> StepExecutionResult | None:
        candidates = [
            step for step in steps if step.attempt_number == target.attempt_number
        ]
        if target.step_id is not None:
            candidates = [step for step in candidates if step.step_id == target.step_id]
        elif target.target_type is VerificationTargetType.INSTALLATION_SUCCEEDS:
            candidates = [
                step for step in candidates if step.action_type == "INSTALL_DEPENDENCY"
            ]
            if not candidates:
                candidates = [
                    step
                    for step in steps
                    if step.attempt_number == target.attempt_number
                    and step.command is not None
                    and re.match(
                        r"(?i)^(?:python\s+-m\s+pip\s+install|pip\s+install|"
                        r"conda\s+env\s+create|poetry\s+install|uv\s+sync)",
                        step.command,
                    )
                ]
        if target.command is not None:
            candidates = [step for step in candidates if step.command == target.command]
        return candidates[0] if len(candidates) == 1 else None

    @staticmethod
    def _identity_error(
        contract: VerificationContract,
        execution: ExecutionRunResult | None,
    ) -> str | None:
        if execution is None:
            return None
        if (
            contract.repository is not None
            and execution.repository != contract.repository
        ):
            return "Execution evidence belongs to a different repository."
        if (
            contract.commit_sha is not None
            and execution.commit_sha != contract.commit_sha
        ):
            return "Execution evidence belongs to a different repository revision."
        return None

    @staticmethod
    def _test_observation(output: str) -> tuple[GoalMilestoneStatus, str]:
        if re.search(r"(?:collected\s+0\s+items|no tests ran)", output, re.IGNORECASE):
            return (
                GoalMilestoneStatus.NO_TESTS_COLLECTED,
                "The test command collected no tests.",
            )
        if re.search(
            r"(?:\b0\s+passed\b.*\b\d+\s+skipped\b|\b\d+\s+skipped\b.*\b0\s+passed\b)",
            output,
            re.IGNORECASE,
        ):
            return GoalMilestoneStatus.ALL_SKIPPED, "All collected tests were skipped."
        return (
            GoalMilestoneStatus.PASSED,
            "Tests executed and the command exited successfully.",
        )

    @classmethod
    def _goal_coverage(
        cls,
        contract: VerificationContract,
        checks: list[VerificationCheck],
    ) -> GoalCoverage | None:
        if not contract.required_milestones:
            return None
        observations: list[GoalMilestoneObservation] = []
        observed: list[str] = []
        for milestone in contract.required_milestones:
            matching = [check for check in checks if check.milestone == milestone]
            check = matching[0] if matching else None
            status = (
                check.milestone_status
                if check is not None and check.milestone_status is not None
                else GoalMilestoneStatus.NOT_EXECUTED
            )
            if status is GoalMilestoneStatus.PASSED:
                observed.append(milestone)
            observations.append(
                GoalMilestoneObservation(
                    milestone=milestone,
                    status=status,
                    step_id=check.step_id if check is not None else None,
                    detail=check.reason
                    if check is not None
                    else "No milestone evidence was recorded.",
                )
            )
        unmet = [item for item in contract.required_milestones if item not in observed]
        coverage_status = (
            "COMPLETE" if not unmet else ("PARTIAL" if observed else "NOT_STARTED")
        )
        return GoalCoverage(
            requested_goal=contract.goal,
            required_milestones=list(contract.required_milestones),
            observed_milestones=observed,
            unmet_milestones=unmet,
            coverage_status=coverage_status,
            observations=observations,
        )

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
