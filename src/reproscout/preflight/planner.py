"""Finite, setup-only planning for environment preflight."""

from __future__ import annotations

import shlex
from pathlib import Path, PurePosixPath

from reproscout.analysis import RepositoryAnalysis
from reproscout.planning import (
    PlanActionType,
    PlanBaseline,
    PlannerLimits,
    PlanProvenance,
    PlanStep,
    ReproductionPlan,
    RiskLevel,
)
from reproscout.planning.safety import (
    PlanSafetyError,
    validate_command,
    validate_working_directory,
)

from .models import PreflightInspection


class PreflightPlanningError(ValueError):
    """Raised when a preflight plan cannot be kept within its safety contract."""


def _relative_dependency_path(value: str) -> str:
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise PreflightPlanningError(
            f"Dependency source is not a safe repository-relative path: {value}."
        )
    return path.as_posix()


def _step(
    *,
    index: int,
    action_type: PlanActionType,
    command: str | None,
    purpose: str,
    expected_outcome: str,
    timeout_seconds: float,
    network_required: bool = False,
    risk: RiskLevel = RiskLevel.LOW,
    source_path: str | None = None,
    working_directory: str = ".",
) -> PlanStep:
    try:
        validate_working_directory(working_directory)
    except PlanSafetyError as exc:
        raise PreflightPlanningError(str(exc)) from exc
    if command is not None:
        try:
            validate_command(command, max_length=2_000)
        except PlanSafetyError as exc:
            raise PreflightPlanningError(str(exc)) from exc
    return PlanStep(
        step_id=f"step-{index:03d}",
        action_type=action_type,
        command=command,
        working_directory=working_directory,
        purpose=purpose,
        timeout_seconds=timeout_seconds,
        network_required=network_required,
        expected_outcome=expected_outcome,
        risk=risk,
        provenance=PlanProvenance.TOOL_GENERATED,
        source_path=source_path,
    )


class PreflightPlanner:
    """Create a plan that cannot select tests, demos, or experiment commands."""

    def __init__(self, limits: PlannerLimits | None = None) -> None:
        self.limits = limits or PlannerLimits(
            max_steps=30,
            per_step_timeout_seconds=300,
            overall_timeout_seconds=1_800,
        )

    def create_plan(
        self,
        analysis: RepositoryAnalysis,
        inspection: PreflightInspection,
    ) -> ReproductionPlan:
        blocking = [
            *inspection.unsafe_findings,
            *inspection.incompatibilities,
            *inspection.missing_prerequisites,
        ]
        steps: list[PlanStep] = []
        if blocking:
            steps.append(
                _step(
                    index=1,
                    action_type=PlanActionType.PREPARE_ENVIRONMENT,
                    command=None,
                    purpose=(
                        "Stop environment preflight until the detected safety or "
                        "prerequisite issue is reviewed."
                    ),
                    expected_outcome=(
                        "No unsafe instruction or unresolved prerequisite remains."
                    ),
                    timeout_seconds=self.limits.per_step_timeout_seconds,
                    risk=RiskLevel.HIGH,
                    source_path=inspection.source_files[0]
                    if inspection.source_files
                    else None,
                )
            )
        else:
            steps.extend(
                [
                    _step(
                        index=1,
                        action_type=PlanActionType.RUN_SETUP,
                        command="python --version",
                        purpose="Verify the isolated Python interpreter.",
                        expected_outcome=(
                            "The preflight Python interpreter reports its version."
                        ),
                        timeout_seconds=self.limits.per_step_timeout_seconds,
                    ),
                    _step(
                        index=2,
                        action_type=PlanActionType.RUN_SETUP,
                        command="python -m pip --version",
                        purpose=(
                            "Verify that pip is available in the isolated interpreter."
                        ),
                        expected_outcome=(
                            "pip reports a version without executing repository code."
                        ),
                        timeout_seconds=self.limits.per_step_timeout_seconds,
                    ),
                ]
            )
            for tool in inspection.system_tools:
                command = f"command -v {shlex.quote(tool)}"
                steps.append(
                    _step(
                        index=len(steps) + 1,
                        action_type=PlanActionType.RUN_SETUP,
                        command=command,
                        purpose=f"Check that required system tool {tool} is on PATH.",
                        expected_outcome=(
                            f"{tool} is available inside the preflight container."
                        ),
                        timeout_seconds=self.limits.per_step_timeout_seconds,
                    )
                )
            for source in inspection.dependency_sources:
                relative = _relative_dependency_path(source)
                name = Path(relative).name.lower()
                working_directory = "."
                if name in {"environment.yml", "environment.yaml"}:
                    continue
                if name.startswith("requirements") and name.endswith(".txt"):
                    command = (
                        "python -m pip install --disable-pip-version-check "
                        f"--no-input --no-cache-dir -r {shlex.quote(relative)}"
                    )
                elif name in {"pyproject.toml", "setup.py", "setup.cfg"}:
                    command = (
                        "python -m pip install --disable-pip-version-check "
                        "--no-input --no-cache-dir ."
                    )
                    working_directory = str(PurePosixPath(relative).parent)
                else:
                    continue
                steps.append(
                    _step(
                        index=len(steps) + 1,
                        action_type=PlanActionType.INSTALL_DEPENDENCY,
                        command=command,
                        purpose=f"Install the declared dependencies from {relative}.",
                        expected_outcome=(
                            "The bounded dependency installation exits with code zero."
                        ),
                        timeout_seconds=self.limits.per_step_timeout_seconds,
                        network_required=True,
                        risk=RiskLevel.MEDIUM,
                        source_path=relative,
                        working_directory=working_directory,
                    )
                )
            if any(
                step.action_type is PlanActionType.INSTALL_DEPENDENCY for step in steps
            ):
                steps.append(
                    _step(
                        index=len(steps) + 1,
                        action_type=PlanActionType.RUN_SETUP,
                        command="python -m pip check",
                        purpose=(
                            "Check installed package metadata for broken requirements."
                        ),
                        expected_outcome="pip check reports no broken requirements.",
                        timeout_seconds=self.limits.per_step_timeout_seconds,
                    )
                )

        if len(steps) > self.limits.max_steps:
            raise PreflightPlanningError(
                f"Preflight contains {len(steps)} steps; limit is "
                f"{self.limits.max_steps}."
            )
        total_timeout = sum(step.timeout_seconds for step in steps)
        if total_timeout > self.limits.overall_timeout_seconds:
            raise PreflightPlanningError(
                "Preflight step timeouts exceed the overall execution budget."
            )
        return ReproductionPlan(
            repository=analysis.repository,
            repository_url=analysis.repository_url,
            commit_sha=analysis.commit_sha,
            goal="environment-preflight",
            requested_goal="environment-preflight",
            baseline=PlanBaseline.STRUCTURED_METADATA,
            steps=steps,
            required_milestones=[],
            resolved_environment=analysis.resolved_environment,
            sandbox_scope="plan",
            overall_timeout_seconds=self.limits.overall_timeout_seconds,
        )
