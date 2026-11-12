"""Objective readiness evaluation for preflight execution evidence."""

from __future__ import annotations

import re

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from reproscout.diagnosis import DiagnosisResult
from reproscout.execution import ExecutionFailureKind, ExecutionRunResult
from reproscout.planning import PlanActionType, ReproductionPlan
from reproscout.security import redact_sensitive_text

from .models import (
    PreflightAssessment,
    PreflightCommandResult,
    PreflightInspection,
    PreflightRequirement,
    PreflightStatus,
    RequirementKind,
    RequirementState,
)


def _result_for_step(execution: ExecutionRunResult | None, step_id: str):
    if execution is None:
        return None
    return next((item for item in execution.steps if item.step_id == step_id), None)


def _command_results(
    plan: ReproductionPlan,
    execution: ExecutionRunResult | None,
) -> list[PreflightCommandResult]:
    results: list[PreflightCommandResult] = []
    for step in plan.steps:
        result = _result_for_step(execution, step.step_id)
        if result is None:
            results.append(
                PreflightCommandResult(
                    step_id=step.step_id,
                    action_type=step.action_type.value,
                    command=step.command,
                    purpose=step.purpose,
                    outcome="NOT_EXECUTED",
                    source_path=step.source_path,
                )
            )
            continue
        outcome = (
            "PASSED"
            if result.failure_kind is None
            and result.exit_code == 0
            and not result.timed_out
            else "FAILED"
        )
        results.append(
            PreflightCommandResult(
                step_id=step.step_id,
                action_type=step.action_type.value,
                command=step.command,
                purpose=step.purpose,
                outcome=outcome,
                exit_code=result.exit_code,
                timed_out=result.timed_out,
                failure_kind=(
                    result.failure_kind.value
                    if result.failure_kind is not None
                    else None
                ),
                stdout=redact_sensitive_text(result.stdout)[:20_000],
                stderr=redact_sensitive_text(result.stderr)[:20_000],
                source_path=step.source_path,
            )
        )
    return results


def _passed(commands: list[PreflightCommandResult], command: str) -> bool:
    return any(
        item.command == command and item.outcome == "PASSED" for item in commands
    )


def _update_requirements(
    inspection: PreflightInspection,
    commands: list[PreflightCommandResult],
    *,
    environment_python: str | None,
    dependencies_verified: bool,
) -> list[PreflightRequirement]:
    updated: list[PreflightRequirement] = []
    tools: dict[str, bool] = {}
    for item in commands:
        if item.command is None or not item.command.startswith("command -v "):
            continue
        tool = item.command.removeprefix("command -v ").strip().strip("'")
        tools[tool] = item.outcome == "PASSED"
    for requirement in inspection.requirements:
        state = requirement.state
        observed = requirement.observed
        detail = requirement.detail
        if requirement.kind is RequirementKind.PYTHON:
            if _passed(commands, "python --version") and environment_python:
                state = RequirementState.VERIFIED
                observed = environment_python
            elif state is RequirementState.UNVERIFIED:
                detail = "The isolated Python version was not objectively verified."
        elif requirement.kind is RequirementKind.SYSTEM_TOOL:
            available = tools.get(requirement.name)
            if available is True:
                state = RequirementState.VERIFIED
                observed = "available on PATH"
            elif available is False:
                state = RequirementState.MISSING
                detail = "The tool was not found on PATH inside the container."
        elif (
            requirement.kind is RequirementKind.PACKAGE
            and not requirement.optional
            and dependencies_verified
        ):
            state = RequirementState.VERIFIED
            observed = "installation and pip check passed"
        updated.append(
            requirement.model_copy(
                update={"state": state, "observed": observed, "detail": detail}
            )
        )
    return updated


def _python_constraints_are_observed(
    constraints: list[str], observed: str | None
) -> bool:
    if not constraints or observed is None:
        return True
    try:
        observed_version = Version(observed)
    except InvalidVersion:
        return False
    for constraint in constraints:
        normalized = constraint.strip()
        if re.fullmatch(r"\d+\.\d+(?:\.\d+)?", normalized):
            normalized = f"=={normalized}"
        try:
            specifier = SpecifierSet(normalized)
        except InvalidSpecifier:
            return False
        if observed_version not in specifier:
            return False
    return True


def _next_steps(status: PreflightStatus) -> list[str]:
    return {
        PreflightStatus.READY: [
            "Give the handoff to the research coding agent as environment context.",
            (
                "Ask for an explicit experiment plan before running tests, demos, "
                "training, or evaluations."
            ),
        ],
        PreflightStatus.PARTIAL: [
            (
                "Review the failed lightweight check and its bounded stderr before "
                "changing dependencies."
            ),
            "Rerun preflight after a deliberate, documented environment change.",
        ],
        PreflightStatus.INCOMPATIBLE: [
            (
                "Select a supported Python/runtime image or document the required "
                "runtime explicitly."
            ),
            (
                "Do not treat dependency upgrades or source edits as evidence of "
                "compatibility."
            ),
        ],
        PreflightStatus.MISSING_PREREQUISITES: [
            (
                "Supply or document the missing prerequisite without copying host "
                "credentials into the sandbox."
            ),
            "Rerun preflight after the prerequisite is available.",
        ],
        PreflightStatus.INSTALLATION_FAILED: [
            (
                "Inspect the installation failure and pinned constraints before "
                "attempting any repair."
            ),
            (
                "Prefer a minimal, reversible dependency or runtime change and rerun "
                "preflight."
            ),
        ],
        PreflightStatus.UNSAFE: [
            "Review the flagged repository instruction manually as untrusted data.",
            "Do not bypass the safety policy by pasting the command into a host shell.",
        ],
        PreflightStatus.INCOMPLETE: [
            "Document or identify a supported dependency manifest and rerun preflight.",
            "Treat package and project capability status as unverified until then.",
        ],
        PreflightStatus.UNVERIFIED: [
            "Resolve the Docker or evidence-collection problem and rerun preflight.",
            "Do not claim environment readiness from an LLM explanation alone.",
        ],
    }[status]


def evaluate_preflight(
    inspection: PreflightInspection,
    plan: ReproductionPlan,
    execution: ExecutionRunResult | None,
    *,
    diagnosis: DiagnosisResult | None = None,
) -> PreflightAssessment:
    """Classify readiness only from deterministic inspection and execution facts."""

    commands = _command_results(plan, execution)
    environment = execution.environment if execution is not None else None
    failed = next((item for item in commands if item.outcome == "FAILED"), None)
    dependencies_attempted = any(
        item.action_type == PlanActionType.INSTALL_DEPENDENCY.value for item in commands
    )
    dependencies_verified = (
        dependencies_attempted
        and not any(
            item.action_type == PlanActionType.INSTALL_DEPENDENCY.value
            and item.outcome != "PASSED"
            for item in commands
        )
        and _passed(commands, "python -m pip check")
    )
    observed_python = environment.python_version if environment else None
    requirements = _update_requirements(
        inspection,
        commands,
        environment_python=observed_python,
        dependencies_verified=dependencies_verified,
    )
    unresolved = [
        *inspection.incompatibilities,
        *inspection.missing_prerequisites,
    ]
    if failed is not None and failed.stderr:
        unresolved.append(
            "Bounded failure output: " + failed.stderr[:2_000].replace("\n", " ")
        )
    verified_facts: list[str] = []
    for item in commands:
        if item.outcome == "PASSED" and item.command is not None:
            verified_facts.append(f"{item.command} exited with code zero.")
    if environment is not None:
        verified_facts.append(
            "Docker environment fingerprint captured with Python "
            f"{environment.python_version}."
        )

    if inspection.unsafe_findings:
        status = PreflightStatus.UNSAFE
        unresolved.extend(inspection.unsafe_findings)
        summary = (
            "Preflight stopped because repository instructions triggered the "
            "safety policy."
        )
    elif inspection.incompatibilities:
        status = PreflightStatus.INCOMPATIBLE
        summary = (
            "Preflight stopped because declared runtime constraints are "
            "incompatible with the supported envelope."
        )
    elif inspection.missing_prerequisites:
        status = PreflightStatus.MISSING_PREREQUISITES
        summary = (
            "Preflight stopped before setup because required prerequisites were "
            "not available."
        )
    elif failed is not None and (
        failed.failure_kind is ExecutionFailureKind.SANDBOX_FAILURE
        or (
            execution is not None
            and execution.failure_kind is ExecutionFailureKind.SANDBOX_FAILURE
        )
    ):
        status = PreflightStatus.UNVERIFIED
        unresolved.append(
            "Docker sandbox initialization or cleanup failed; command outcomes "
            "are not sufficient to verify the environment."
        )
        summary = (
            "Environment readiness is unverified because the isolated sandbox "
            "did not initialize or remain available for the check."
        )
    elif failed is not None and (
        failed.action_type == PlanActionType.INSTALL_DEPENDENCY.value
        or failed.command == "python -m pip check"
    ):
        status = PreflightStatus.INSTALLATION_FAILED
        unresolved.append(
            "Dependency installation or pip check failed; package readiness is "
            "not verified."
        )
        summary = "The isolated dependency setup failed objectively."
    elif (
        failed is not None
        and failed.command is not None
        and failed.command.startswith("command -v ")
    ):
        status = PreflightStatus.MISSING_PREREQUISITES
        unresolved.append(
            "Required system tool was not found: "
            f"{failed.command.removeprefix('command -v ')}."
        )
        summary = "A required system tool is missing from the isolated environment."
    elif execution is None or execution.workflow_succeeded is not True:
        sandbox_failed = (
            execution is not None
            and execution.failure_kind is ExecutionFailureKind.SANDBOX_FAILURE
        )
        status = (
            PreflightStatus.UNVERIFIED
            if failed is None or sandbox_failed
            else PreflightStatus.PARTIAL
        )
        unresolved.append(
            "The permitted preflight checks did not complete successfully."
        )
        summary = (
            "Environment readiness is not verified because preflight did not complete."
        )
    elif environment is None:
        status = PreflightStatus.UNVERIFIED
        unresolved.append("No Docker environment fingerprint was captured.")
        summary = (
            "Commands completed, but the environment itself was not objectively "
            "fingerprinted."
        )
    elif not inspection.dependency_sources:
        status = PreflightStatus.INCOMPLETE
        unresolved.append("No supported dependency source was detected.")
        summary = (
            "The base interpreter is visible, but package environment readiness "
            "is incomplete."
        )
    elif not _passed(commands, "python --version") or not _passed(
        commands, "python -m pip --version"
    ):
        status = PreflightStatus.UNVERIFIED
        unresolved.append("Python or pip verification did not pass.")
        summary = "The isolated Python package environment is not verified."
    elif not _python_constraints_are_observed(
        inspection.python_constraints, observed_python
    ):
        status = PreflightStatus.INCOMPATIBLE
        unresolved.append(
            f"Observed Python {observed_python} does not satisfy the declared "
            "constraints."
        )
        summary = (
            "The observed Python runtime does not satisfy the repository constraints."
        )
    elif not dependencies_verified:
        status = PreflightStatus.INCOMPLETE
        unresolved.append("Dependency installation evidence is incomplete.")
        summary = (
            "The dependency source was found, but successful installation and "
            "pip check evidence is incomplete."
        )
    elif inspection.dependency_parse_errors:
        status = PreflightStatus.PARTIAL
        unresolved.append(
            "Some dependency declarations could not be statically inspected."
        )
        summary = (
            "The isolated installation passed, but part of the declared "
            "dependency metadata remains unverified."
        )
    elif inspection.optional_package_requirements:
        status = PreflightStatus.PARTIAL
        unresolved.append(
            "Optional dependencies were detected but were not installed by default."
        )
        summary = (
            "Required dependencies passed, but optional package capabilities remain "
            "unverified."
        )
    else:
        status = PreflightStatus.READY
        summary = (
            "The declared environment was installed and lightweight checks passed; "
            "project tests, demos, training, and evaluations were not run."
        )

    unverified = list(inspection.unverified_capabilities)
    if diagnosis is not None:
        unverified.append(
            "Optional diagnosis is a hypothesis and does not establish readiness."
        )
    return PreflightAssessment(
        status=status,
        summary=summary,
        verified_environment=status is PreflightStatus.READY,
        requirements=requirements,
        commands=commands,
        environment=environment,
        verified_facts=verified_facts,
        unresolved_problems=list(dict.fromkeys(unresolved)),
        unverified_capabilities=list(dict.fromkeys(unverified)),
        safe_next_steps=_next_steps(status),
        diagnosis=diagnosis,
    )
