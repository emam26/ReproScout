"""Observed evaluation results and deterministic aggregate metrics."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from reproscout.diagnostics.models import DiagnosticModel, FailureClass
from reproscout.status import ReproductionStatus
from reproscout.verification import VerificationLevel


class EvaluationStage(StrEnum):
    """Optional stage facts that a system adapter may report."""

    INTAKE = "INTAKE"
    ANALYZE = "ANALYZE"
    ENVIRONMENT_SETUP = "ENVIRONMENT_SETUP"
    EXECUTE = "EXECUTE"
    TESTS = "TESTS"


class EvaluationObservation(DiagnosticModel):
    """Observed facts for one case, supplied by a controlled system adapter."""

    case_id: str = Field(pattern=r"^eval-[0-9]{3}$")
    observed_status: ReproductionStatus
    observed_verification_level: VerificationLevel
    failure_category: FailureClass | None = None
    repair_attempted: bool = False
    repair_succeeded: bool = False
    human_intervention: bool = False
    agent_steps: int = Field(ge=0)
    llm_calls: int = Field(ge=0)
    duration_seconds: float = Field(ge=0)
    stage_outcomes: dict[str, bool | None] = Field(default_factory=dict, max_length=10)

    @model_validator(mode="after")
    def _repair_success_requires_attempt(self) -> Self:
        if self.repair_succeeded and not self.repair_attempted:
            raise ValueError("A successful repair requires a repair attempt.")
        invalid_stages = set(self.stage_outcomes) - {
            stage.value for stage in EvaluationStage
        }
        if invalid_stages:
            raise ValueError(f"Unknown evaluation stages: {sorted(invalid_stages)}")
        return self


class EvaluationMismatch(DiagnosticModel):
    """One explicit disagreement between a contract and an observation."""

    case_id: str = Field(pattern=r"^eval-[0-9]{3}$")
    expected_status: ReproductionStatus
    observed_status: ReproductionStatus
    expected_verification_level: VerificationLevel
    observed_verification_level: VerificationLevel
    status_mismatch: bool
    verification_mismatch: bool


class StageEvaluationMetric(DiagnosticModel):
    """Success rate for a stage among cases that reported that stage."""

    stage: EvaluationStage
    evaluated_cases: int = Field(ge=0)
    successful_cases: int = Field(ge=0)
    success_rate: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def _counts_are_consistent(self) -> Self:
        if self.successful_cases > self.evaluated_cases:
            raise ValueError("Successful stage cases cannot exceed evaluated cases.")
        if self.evaluated_cases == 0 and self.success_rate is not None:
            raise ValueError("An unobserved stage cannot have a success rate.")
        return self


class EvaluationMetrics(DiagnosticModel):
    """Deterministic aggregate measures for one complete evaluation set."""

    case_count: int = Field(ge=1)
    status_accuracy: float = Field(ge=0, le=1)
    verification_accuracy: float = Field(ge=0, le=1)
    expected_status_counts: dict[str, int]
    observed_status_counts: dict[str, int]
    confusion_matrix: dict[str, dict[str, int]]
    failure_category_counts: dict[str, int]
    repair_case_count: int = Field(ge=0)
    repair_success_count: int = Field(ge=0)
    repair_success_rate: float | None = Field(default=None, ge=0, le=1)
    human_intervention_count: int = Field(ge=0)
    human_intervention_rate: float = Field(ge=0, le=1)
    mean_duration_seconds: float = Field(ge=0)
    total_agent_steps: int = Field(ge=0)
    total_llm_calls: int = Field(ge=0)
    stage_metrics: list[StageEvaluationMetric]

    @model_validator(mode="after")
    def _metric_counts_are_consistent(self) -> Self:
        if self.repair_success_count > self.repair_case_count:
            raise ValueError("Repair successes cannot exceed repair cases.")
        if self.repair_case_count == 0 and self.repair_success_rate is not None:
            raise ValueError("An empty repair stratum cannot have a success rate.")
        if self.human_intervention_count > self.case_count:
            raise ValueError("Interventions cannot exceed the number of cases.")
        return self


class EvaluationReport(DiagnosticModel):
    """Observed evaluation data plus metrics and explicit disagreements."""

    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    evaluation_set: str = Field(min_length=1, max_length=100)
    observations: list[EvaluationObservation] = Field(min_length=1, max_length=500)
    metrics: EvaluationMetrics
    mismatches: list[EvaluationMismatch] = Field(max_length=500)
