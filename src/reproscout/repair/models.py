"""Typed, bounded repair proposals and experiment observations."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import Field, JsonValue

from reproscout.diagnostics import FailureClass
from reproscout.diagnostics.models import DiagnosticModel
from reproscout.security import reject_sensitive_mapping


class RepairActionType(StrEnum):
    """Finite repair operations exposed to Phase 9."""

    CHANGE_INVOCATION = "CHANGE_INVOCATION"
    CHANGE_PYTHON_VERSION = "CHANGE_PYTHON_VERSION"
    ADD_DEPENDENCY = "ADD_DEPENDENCY"
    CHANGE_DEPENDENCY_VERSION = "CHANGE_DEPENDENCY_VERSION"
    SET_SAFE_ENVIRONMENT_VARIABLE = "SET_SAFE_ENVIRONMENT_VARIABLE"
    CREATE_REQUIRED_DIRECTORY = "CREATE_REQUIRED_DIRECTORY"
    ADJUST_CONFIG_PATH = "ADJUST_CONFIG_PATH"
    FETCH_DOCUMENTED_ASSET = "FETCH_DOCUMENTED_ASSET"
    APPLY_MINIMAL_PATCH = "APPLY_MINIMAL_PATCH"
    GATHER_MORE_EVIDENCE = "GATHER_MORE_EVIDENCE"
    STOP_UNREPAIRABLE = "STOP_UNREPAIRABLE"


class RepairRisk(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Reversibility(StrEnum):
    NONE = "NONE"
    REVERTIBLE = "REVERTIBLE"
    ROLLBACK_REQUIRED = "ROLLBACK_REQUIRED"


class RepairAction(DiagnosticModel):
    """A policy-checkable repair proposal, not an arbitrary command."""

    action_id: str = Field(pattern=r"^repair-[0-9]{3}$")
    action_type: RepairActionType
    reason: Annotated[str, Field(min_length=1, max_length=2_000)]
    supporting_evidence: list[str] = Field(min_length=1, max_length=20)
    expected_effect: Annotated[str, Field(min_length=1, max_length=2_000)]
    risk: RepairRisk
    reversibility: Reversibility
    arguments: dict[str, JsonValue] = Field(default_factory=dict)

    def __init__(self, **data: object) -> None:
        super().__init__(**data)
        reject_sensitive_mapping(self.arguments)


class RepairObservation(DiagnosticModel):
    """Objective result returned by the controlled execution/verifier boundary."""

    workflow_succeeded: bool
    failure_class: FailureClass | None = None
    failure_signature: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    exit_code: int | None = None
    summary: str = Field(min_length=1, max_length=2_000)


class RepairExperimentStatus(StrEnum):
    APPLIED = "APPLIED"
    IMPROVED = "IMPROVED"
    UNCHANGED = "UNCHANGED"
    REJECTED = "REJECTED"
    STOPPED = "STOPPED"
    DEFERRED = "DEFERRED"


class RepairExperimentResult(DiagnosticModel):
    """Evidence-backed outcome of one bounded repair experiment."""

    action: RepairAction
    status: RepairExperimentStatus
    before_failure_signature: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
    after_failure_signature: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    observation: RepairObservation | None = None
    evidence_improved: bool = False
    rollback_performed: bool = False
    policy_rejection: str | None = Field(default=None, max_length=1_000)
    stop_reason: str | None = Field(default=None, max_length=1_000)


class RepairLimits(DiagnosticModel):
    """Hard bounds for one repair experiment session."""

    max_repairs: int = Field(default=3, ge=1, le=20)
    max_repeated_failure_signatures: int = Field(default=1, ge=1, le=10)
    max_patch_characters: int = Field(default=4_000, ge=100, le=100_000)
    max_download_bytes: int = Field(default=50_000_000, ge=1_024, le=10_000_000_000)
    max_risk: RepairRisk = RepairRisk.MEDIUM
