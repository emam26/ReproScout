"""Strict report inputs and artifact-path models."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from reproscout.diagnosis import DiagnosisResult
from reproscout.diagnostics.models import DiagnosticModel, FailureClass
from reproscout.planning import PlanActionType, PlanProvenance
from reproscout.repair import RepairExperimentResult
from reproscout.status import ReproductionStatusResult
from reproscout.verification import GoalCoverage, VerificationResult


class ReportAttemptSource(StrEnum):
    OFFICIAL_DOCUMENTED = "OFFICIAL_DOCUMENTED"
    AGENT_ASSISTED = "AGENT_ASSISTED"


class ReportAttempt(DiagnosticModel):
    """One real attempt summary included in the report."""

    attempt_id: str = Field(min_length=1, max_length=200)
    source: ReportAttemptSource
    description: str = Field(min_length=1, max_length=2_000)
    commands: list[str] = Field(default_factory=list, max_length=100)
    workflow_succeeded: bool | None = None


class ReportFailure(DiagnosticModel):
    """One bounded failure summary and its evidence references."""

    failure_id: str = Field(min_length=1, max_length=200)
    failure_class: FailureClass | None = None
    detail: str = Field(min_length=1, max_length=2_000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=50)


class ReportPlanStep(DiagnosticModel):
    """Small, report-facing view of one planned action and its provenance."""

    step_id: str = Field(pattern=r"^step-[0-9]{3}$")
    action_type: PlanActionType
    command: str | None = Field(default=None, max_length=2_000)
    provenance: PlanProvenance
    source_path: str | None = Field(default=None, max_length=500)
    source_location: str | None = Field(default=None, max_length=200)
    attempt_number: int = Field(default=1, ge=1)


class RunReport(DiagnosticModel):
    """Schema-versioned report source data; no final status is inferred here."""

    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    run_id: str = Field(min_length=1, max_length=200)
    repository: str = Field(min_length=1, max_length=500)
    commit_sha: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=500)
    documented_setup: list[str] = Field(default_factory=list, max_length=100)
    plan_steps: list[ReportPlanStep] = Field(default_factory=list, max_length=100)
    initial_attempt: ReportAttempt
    agent_assisted_attempts: list[ReportAttempt] = Field(
        default_factory=list, max_length=100
    )
    failures: list[ReportFailure] = Field(default_factory=list, max_length=100)
    diagnoses: list[DiagnosisResult] = Field(default_factory=list, max_length=50)
    repairs: list[RepairExperimentResult] = Field(default_factory=list, max_length=50)
    verification: VerificationResult | None = None
    goal_coverage: GoalCoverage | None = None
    final_status: ReproductionStatusResult | None = None
    blockers: list[str] = Field(default_factory=list, max_length=100)
    documentation_gaps: list[str] = Field(default_factory=list, max_length=100)


class ReportArtifactPaths(DiagnosticModel):
    """Files actually written by one report operation."""

    run_directory: str
    written_files: list[str] = Field(min_length=2, max_length=20)
