"""Deterministic reproduction planner with documented-first ordering."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from reproscout.analysis import (
    AnalysisEvidence,
    EvidenceProvenance,
    RepositoryAnalysis,
)

from .models import (
    PlanActionType,
    PlanBaseline,
    PlannerLimits,
    PlanProvenance,
    PlanStep,
    ReproductionPlan,
    RiskLevel,
)
from .safety import PlanSafetyError, validate_command, validate_working_directory


class PlanningError(RuntimeError):
    """Base error for invalid or unbounded reproduction plans."""


class PlanLimitError(PlanningError):
    """Raised when a plan exceeds a configured finite bound."""


class UnsafePlanError(PlanningError):
    """Raised when a plan requests a prohibited command or path."""


@dataclass(frozen=True, slots=True)
class _ProposedStep:
    action_type: PlanActionType
    command: str | None
    purpose: str
    expected_outcome: str
    evidence: tuple[AnalysisEvidence, ...]
    network_required: bool = False
    risk: RiskLevel = RiskLevel.LOW


def _effective_goal(analysis: RepositoryAnalysis, requested_goal: str) -> str:
    normalized = requested_goal.strip().lower()
    return normalized


def _required_milestones(goal: str) -> list[str]:
    return {
        "install": ["install"],
        "tests": ["tests"],
        "demo": ["demo"],
        "auto": [],
    }.get(goal, [])


def _provenance(evidence: tuple[AnalysisEvidence, ...]) -> PlanProvenance:
    if not evidence:
        return PlanProvenance.TOOL_GENERATED
    if evidence[0].provenance is EvidenceProvenance.DOCUMENTED:
        return PlanProvenance.DOCUMENTED
    if evidence[0].provenance is EvidenceProvenance.LLM_INFERRED:
        return PlanProvenance.INFERRED
    return PlanProvenance.TOOL_GENERATED


def _source(evidence: tuple[AnalysisEvidence, ...]) -> tuple[str | None, str | None]:
    if not evidence:
        return None, None
    return evidence[0].source_path, evidence[0].source_location


def _source_priority(evidence: AnalysisEvidence) -> tuple[int, int, str]:
    provenance_rank = {
        EvidenceProvenance.DOCUMENTED: 0,
        EvidenceProvenance.DETERMINISTICALLY_DETECTED: 1,
        EvidenceProvenance.LLM_INFERRED: 2,
    }[evidence.provenance]
    source = (evidence.source_path or "").lower()
    if Path(source).name.startswith(("readme", "install")):
        source_rank = 0
    elif source.startswith(".github/workflows/"):
        source_rank = 2
    else:
        source_rank = 1
    return provenance_rank, source_rank, source


def _evidence_for(
    analysis: RepositoryAnalysis,
    category: str,
    value: str,
) -> tuple[AnalysisEvidence, ...]:
    matching = [
        evidence
        for evidence in analysis.evidence
        if evidence.category == category and evidence.value == value
    ]
    return tuple(sorted(matching, key=_source_priority))


def _ordered_values(
    analysis: RepositoryAnalysis,
    category: str,
    values: list[str],
) -> list[str]:
    indexed = {value: index for index, value in enumerate(values)}

    def priority(value: str) -> tuple[int, int, str, int]:
        evidence = _evidence_for(analysis, category, value)
        if evidence:
            source_priority = _source_priority(evidence[0])
            return (*source_priority, indexed[value])
        return 3, 3, "", indexed[value]

    return sorted(values, key=priority)


def _baseline(analysis: RepositoryAnalysis) -> PlanBaseline:
    if any(
        evidence.provenance is EvidenceProvenance.DOCUMENTED
        and evidence.category in {"install_command", "run_command", "test_command"}
        for evidence in analysis.evidence
    ):
        return PlanBaseline.OFFICIAL_DOCUMENTATION
    if any(
        evidence.provenance is EvidenceProvenance.DETERMINISTICALLY_DETECTED
        for evidence in analysis.evidence
    ):
        return PlanBaseline.STRUCTURED_METADATA
    return PlanBaseline.EXPLICIT_INFERENCE


class ReproductionPlanner:
    """Convert analysis into a finite plan without executing any command."""

    def __init__(self, limits: PlannerLimits | None = None) -> None:
        self.limits = limits or PlannerLimits()

    def create_plan(
        self,
        analysis: RepositoryAnalysis,
        *,
        goal: str = "auto",
    ) -> ReproductionPlan:
        effective_goal = _effective_goal(analysis, goal)
        proposed: list[_ProposedStep] = []
        if analysis.gpu_required:
            proposed.append(
                _ProposedStep(
                    action_type=PlanActionType.PREPARE_ENVIRONMENT,
                    command=None,
                    purpose="Confirm a compatible GPU/CUDA execution environment.",
                    expected_outcome="A compatible GPU runtime is available before setup.",
                    evidence=tuple(
                        item
                        for item in analysis.evidence
                        if item.category == "gpu_requirement"
                    ),
                    risk=RiskLevel.HIGH,
                )
            )
        unresolved_environment = (
            analysis.resolved_environment.unresolved_requirements
            if analysis.resolved_environment is not None
            else []
        )
        if unresolved_environment and not analysis.gpu_required:
            proposed.append(
                _ProposedStep(
                    action_type=PlanActionType.PREPARE_ENVIRONMENT,
                    command=None,
                    purpose="Resolve unsupported or conflicting environment requirements before execution.",
                    expected_outcome="All environment requirements are supported and resolved.",
                    evidence=(
                        tuple(analysis.resolved_environment.evidence)
                        if analysis.resolved_environment is not None
                        else ()
                    ),
                    risk=RiskLevel.HIGH,
                )
            )
        for asset in analysis.external_assets:
            proposed.append(
                _ProposedStep(
                    action_type=PlanActionType.PREPARE_ASSET,
                    command=None,
                    purpose=f"Make the documented external asset available: {asset}",
                    expected_outcome="The required asset is available in the workspace.",
                    evidence=_evidence_for(analysis, "external_asset", asset),
                    network_required=True,
                    risk=RiskLevel.MEDIUM,
                )
            )
        install_commands = _ordered_values(
            analysis, "install_command", analysis.install_commands
        )
        if effective_goal in {"auto", "install", "tests", "demo"}:
            selected_install_commands = install_commands
        else:
            selected_install_commands = []
        for command in selected_install_commands:
            proposed.append(
                _ProposedStep(
                    action_type=PlanActionType.INSTALL_DEPENDENCY,
                    command=command,
                    purpose="Install dependencies using recorded repository evidence.",
                    expected_outcome="Dependency installation exits with code zero.",
                    evidence=_evidence_for(analysis, "install_command", command),
                    network_required=True,
                    risk=RiskLevel.MEDIUM,
                )
            )
        run_commands = _ordered_values(analysis, "run_command", analysis.run_commands)
        selected_run_commands = (
            run_commands if effective_goal in {"auto", "demo"} else []
        )
        for command in selected_run_commands:
            proposed.append(
                _ProposedStep(
                    action_type=PlanActionType.RUN_DEMO,
                    command=command,
                    purpose="Attempt the recorded project execution procedure.",
                    expected_outcome="The documented target exits with code zero.",
                    evidence=_evidence_for(analysis, "run_command", command),
                    network_required=analysis.network_required,
                )
            )
        if (
            effective_goal in {"auto", "demo"}
            and not run_commands
            and analysis.entrypoints
        ):
            entrypoint = analysis.entrypoints[0]
            command = entrypoint.split(" = ", 1)[0]
            proposed.append(
                _ProposedStep(
                    action_type=PlanActionType.RUN_DEMO,
                    command=command,
                    purpose="Attempt the package entrypoint declared by project metadata.",
                    expected_outcome="The declared entrypoint exits with code zero.",
                    evidence=_evidence_for(analysis, "entrypoint", entrypoint),
                    network_required=analysis.network_required,
                )
            )
        test_commands = _ordered_values(
            analysis, "test_command", analysis.test_commands
        )
        selected_test_commands = (
            test_commands if effective_goal in {"auto", "tests"} else []
        )
        for command in selected_test_commands:
            proposed.append(
                _ProposedStep(
                    action_type=PlanActionType.RUN_TESTS,
                    command=command,
                    purpose="Run the recorded project test command.",
                    expected_outcome="The test command exits with code zero.",
                    evidence=_evidence_for(analysis, "test_command", command),
                )
            )
        if effective_goal == "install" and not selected_install_commands:
            proposed.append(
                _ProposedStep(
                    action_type=PlanActionType.INSTALL_DEPENDENCY,
                    command=None,
                    purpose="No supported installation command was detected.",
                    expected_outcome="A supported installation procedure is available.",
                    evidence=(),
                    risk=RiskLevel.MEDIUM,
                )
            )
        elif effective_goal == "tests" and not selected_test_commands:
            proposed.append(
                _ProposedStep(
                    action_type=PlanActionType.RUN_TESTS,
                    command=None,
                    purpose="No test command was detected for the requested tests goal.",
                    expected_outcome="A project test command is executed and passes.",
                    evidence=(),
                    risk=RiskLevel.MEDIUM,
                )
            )
        elif (
            effective_goal == "demo"
            and not selected_run_commands
            and not analysis.entrypoints
        ):
            proposed.append(
                _ProposedStep(
                    action_type=PlanActionType.RUN_DEMO,
                    command=None,
                    purpose="No demo command or entrypoint was detected for the requested demo goal.",
                    expected_outcome="A documented demo command is executed successfully.",
                    evidence=(),
                    risk=RiskLevel.MEDIUM,
                )
            )
        elif effective_goal == "auto" and not any(step.command for step in proposed):
            proposed.append(
                _ProposedStep(
                    action_type=PlanActionType.VERIFY_BASIC_EXECUTION,
                    command=None,
                    purpose="Select an execution target before attempting reproduction.",
                    expected_outcome="A human or later bounded inference selects a target.",
                    evidence=(),
                    risk=RiskLevel.MEDIUM,
                )
            )
        if len(proposed) > self.limits.max_steps:
            raise PlanLimitError(
                f"Plan contains {len(proposed)} steps; limit is {self.limits.max_steps}."
            )
        steps: list[PlanStep] = []
        for index, proposal in enumerate(proposed, start=1):
            try:
                validate_working_directory(".")
                if proposal.command is not None:
                    validate_command(
                        proposal.command,
                        max_length=self.limits.max_command_length,
                    )
            except PlanSafetyError as exc:
                raise UnsafePlanError(str(exc)) from exc
            steps.append(
                PlanStep(
                    step_id=f"step-{index:03d}",
                    action_type=proposal.action_type,
                    command=proposal.command,
                    working_directory=".",
                    purpose=proposal.purpose,
                    evidence=list(proposal.evidence),
                    timeout_seconds=self.limits.per_step_timeout_seconds,
                    network_required=proposal.network_required,
                    expected_outcome=proposal.expected_outcome,
                    risk=proposal.risk,
                    provenance=_provenance(proposal.evidence),
                    source_path=_source(proposal.evidence)[0],
                    source_location=_source(proposal.evidence)[1],
                )
            )
        total_budget = sum(step.timeout_seconds for step in steps)
        if total_budget > self.limits.overall_timeout_seconds:
            raise PlanLimitError(
                "Sum of per-step timeouts exceeds the overall execution budget."
            )
        return ReproductionPlan(
            repository=analysis.repository,
            repository_url=analysis.repository_url,
            commit_sha=analysis.commit_sha,
            goal=effective_goal,
            requested_goal=goal,
            baseline=_baseline(analysis),
            steps=steps,
            required_milestones=_required_milestones(effective_goal),
            overall_timeout_seconds=self.limits.overall_timeout_seconds,
            resolved_environment=analysis.resolved_environment,
        )
