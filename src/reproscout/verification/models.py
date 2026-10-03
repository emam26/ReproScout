"""Strict models for objective verification evidence and outcomes."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from reproscout.diagnostics.models import DiagnosticModel, VerificationTargetType


class VerificationLevel(StrEnum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


class VerificationCheckStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    UNAVAILABLE = "UNAVAILABLE"


class VerificationResultStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    UNAVAILABLE = "UNAVAILABLE"
    UNSPECIFIED = "UNSPECIFIED"


class GoalMilestoneStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    NOT_EXECUTED = "NOT_EXECUTED"
    NO_TESTS_COLLECTED = "NO_TESTS_COLLECTED"
    ALL_SKIPPED = "ALL_SKIPPED"
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"


class GoalMilestoneObservation(DiagnosticModel):
    milestone: str = Field(min_length=1, max_length=100)
    status: GoalMilestoneStatus
    step_id: str | None = Field(default=None, max_length=100)
    detail: str = Field(min_length=1, max_length=2_000)


class GoalCoverage(DiagnosticModel):
    requested_goal: str = Field(min_length=1, max_length=100)
    required_milestones: list[str] = Field(default_factory=list, max_length=10)
    observed_milestones: list[str] = Field(default_factory=list, max_length=10)
    unmet_milestones: list[str] = Field(default_factory=list, max_length=10)
    coverage_status: str = Field(pattern=r"^(COMPLETE|PARTIAL|NOT_STARTED)$")
    observations: list[GoalMilestoneObservation] = Field(
        default_factory=list, max_length=20
    )


class VerificationEvidence(DiagnosticModel):
    """One bounded, machine-observed fact supporting a check."""

    evidence_id: str = Field(pattern=r"^evidence-[0-9]{3}$")
    source: str = Field(min_length=1, max_length=200)
    detail: str = Field(min_length=1, max_length=2_000)


class VerificationCheck(DiagnosticModel):
    """One target evaluation with an explicit non-LLM status."""

    check_id: str = Field(pattern=r"^check-[0-9]{3}$")
    target_id: str = Field(pattern=r"^verify-[0-9]{3}$")
    step_id: str | None = Field(default=None, max_length=100)
    target_type: VerificationTargetType
    level: VerificationLevel
    required: bool = True
    status: VerificationCheckStatus
    reason: str = Field(min_length=1, max_length=2_000)
    evidence: list[VerificationEvidence] = Field(min_length=1, max_length=10)
    milestone: str | None = Field(default=None, max_length=100)
    milestone_status: GoalMilestoneStatus | None = None

    @model_validator(mode="after")
    def _status_matches_pass_flag(self) -> VerificationCheck:
        if self.status is VerificationCheckStatus.PASSED and not self.evidence:
            raise ValueError("A passed verification check requires evidence.")
        return self


class VerificationResult(DiagnosticModel):
    """Aggregate objective result, deliberately separate from run outcome."""

    status: VerificationResultStatus
    level: VerificationLevel
    contract_goal: str = Field(min_length=1, max_length=200)
    checks: list[VerificationCheck] = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=2_000)
    execution_run_id: str | None = Field(default=None, max_length=200)
    goal_coverage: GoalCoverage | None = None

    @model_validator(mode="after")
    def _check_ids_are_sequential(self) -> VerificationResult:
        expected = [f"check-{index:03d}" for index in range(1, len(self.checks) + 1)]
        if [check.check_id for check in self.checks] != expected:
            raise ValueError("Verification check IDs must be sequential.")
        return self
