"""Strict diagnosis and bounded-loop models."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import Field, JsonValue, model_validator

from reproscout.diagnostics.models import DiagnosticModel, FailureClass
from reproscout.llm import LLMUsage
from reproscout.security import reject_sensitive_mapping


class DiagnosisAction(StrEnum):
    """Finite future repair actions that diagnosis may recommend."""

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


class DiagnosisRisk(StrEnum):
    """Risk level used by policy before any repair exists."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class DiagnosisHypothesis(DiagnosticModel):
    """Short auditable explanation tied to compact evidence references."""

    hypothesis_id: str = Field(pattern=r"^hypothesis-[0-9]{3}$")
    statement: Annotated[str, Field(min_length=1, max_length=1_000)]
    supporting_evidence: list[str] = Field(min_length=1, max_length=10)
    confidence: Annotated[float, Field(ge=0, le=1)] = 0.5


class Diagnosis(DiagnosticModel):
    """Validated diagnosis; it is a proposal, never execution evidence."""

    failure_class: FailureClass
    summary: Annotated[str, Field(min_length=1, max_length=2_000)]
    hypotheses: list[DiagnosisHypothesis] = Field(min_length=1, max_length=5)
    supporting_evidence: list[str] = Field(min_length=1, max_length=20)
    recommended_action: DiagnosisAction
    action_arguments: dict[str, JsonValue] = Field(default_factory=dict)
    expected_observation: Annotated[str, Field(min_length=1, max_length=2_000)]
    confidence: Annotated[float, Field(ge=0, le=1)]
    risk: DiagnosisRisk

    @model_validator(mode="after")
    def _validate_hypotheses_and_arguments(self) -> Diagnosis:
        expected = [
            f"hypothesis-{index:03d}" for index in range(1, len(self.hypotheses) + 1)
        ]
        if [hypothesis.hypothesis_id for hypothesis in self.hypotheses] != expected:
            raise ValueError("Diagnosis hypothesis IDs must be sequential.")
        reject_sensitive_mapping(self.action_arguments)
        return self


class DiagnosisStatus(StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    LLM = "LLM"
    STOPPED = "STOPPED"
    POLICY_REJECTED = "POLICY_REJECTED"
    PROVIDER_ERROR = "PROVIDER_ERROR"


class DiagnosisResult(DiagnosticModel):
    """Outcome of one bounded diagnosis attempt."""

    status: DiagnosisStatus
    diagnosis: Diagnosis | None = None
    provider: str | None = Field(default=None, max_length=50)
    model: str | None = Field(default=None, max_length=200)
    usage: LLMUsage | None = None
    policy_rejection: str | None = Field(default=None, max_length=1_000)
    stop_reason: str | None = Field(default=None, max_length=1_000)


class DiagnosisLimits(DiagnosticModel):
    """Finite limits for one diagnosis session."""

    max_llm_calls: int = Field(default=3, ge=0, le=20)
    max_diagnosis_attempts: int = Field(default=5, ge=1, le=50)
    max_repeated_failure_signatures: int = Field(default=2, ge=1, le=10)
    max_repeated_recommendations: int = Field(default=2, ge=1, le=10)
    max_context_size: int = Field(default=16_000, ge=500, le=100_000)
    max_hypotheses: int = Field(default=5, ge=1, le=10)
    max_risk: DiagnosisRisk = DiagnosisRisk.MEDIUM
