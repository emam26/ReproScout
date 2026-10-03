"""Strict machine-readable status and reason models."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from reproscout.diagnostics.models import DiagnosticModel, FailureClass
from reproscout.verification import (
    GoalCoverage,
    VerificationLevel,
    VerificationResultStatus,
)


class ReproductionStatus(StrEnum):
    REPRODUCED = "REPRODUCED"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    UNSAFE = "UNSAFE"


class StatusReasonCode(StrEnum):
    OBJECTIVE_VERIFICATION_PASSED = "OBJECTIVE_VERIFICATION_PASSED"
    OBJECTIVE_VERIFICATION_FAILED = "OBJECTIVE_VERIFICATION_FAILED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    VERIFICATION_UNAVAILABLE = "VERIFICATION_UNAVAILABLE"
    VERIFICATION_UNSPECIFIED = "VERIFICATION_UNSPECIFIED"
    WORKFLOW_NOT_CONFIRMED = "WORKFLOW_NOT_CONFIRMED"
    CLEAN_ROOM_REQUIRED = "CLEAN_ROOM_REQUIRED"
    CLEAN_ROOM_FAILED = "CLEAN_ROOM_FAILED"
    VERIFICATION_LEVEL_INSUFFICIENT = "VERIFICATION_LEVEL_INSUFFICIENT"
    SAFETY_POLICY_VIOLATION = "SAFETY_POLICY_VIOLATION"
    FAILURE_CLASS = "FAILURE_CLASS"
    GOAL_NOT_COVERED = "GOAL_NOT_COVERED"


class StatusReason(DiagnosticModel):
    """One machine-readable reason tied to objective evidence."""

    reason_id: str = Field(pattern=r"^reason-[0-9]{3}$")
    code: StatusReasonCode
    detail: str = Field(min_length=1, max_length=2_000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=50)
    failure_class: FailureClass | None = None


class ReproductionStatusResult(DiagnosticModel):
    """Final status decision, independent of ``RunOutcome``."""

    status: ReproductionStatus
    reasons: list[StatusReason] = Field(min_length=1, max_length=20)
    verification_status: VerificationResultStatus | None = None
    verification_level: VerificationLevel | None = None
    workflow_succeeded: bool | None = None
    clean_room_required: bool = False
    clean_room_verified: bool | None = None
    goal_coverage: GoalCoverage | None = None

    @model_validator(mode="after")
    def _reason_ids_are_sequential(self) -> ReproductionStatusResult:
        expected = [f"reason-{index:03d}" for index in range(1, len(self.reasons) + 1)]
        if [reason.reason_id for reason in self.reasons] != expected:
            raise ValueError("Status reason IDs must be sequential.")
        return self
