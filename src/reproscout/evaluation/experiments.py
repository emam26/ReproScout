"""Predeclared baselines, ablations, and paired evaluation comparisons."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from reproscout.diagnostics.models import DiagnosticModel
from reproscout.status import ReproductionStatus

from .results import EvaluationReport


class ComparisonMetric(StrEnum):
    """Metrics that may be compared before any results are observed."""

    STATUS_ACCURACY = "status_accuracy"
    VERIFICATION_ACCURACY = "verification_accuracy"
    REPAIR_SUCCESS_RATE = "repair_success_rate"
    HUMAN_INTERVENTION_RATE = "human_intervention_rate"
    MEAN_DURATION_SECONDS = "mean_duration_seconds"


class RemovedCapability(StrEnum):
    """One capability disabled by a paired ablation."""

    LLM_DIAGNOSIS = "LLM_DIAGNOSIS"
    CONTROLLED_REPAIRS = "CONTROLLED_REPAIRS"
    CLEAN_ROOM = "CLEAN_ROOM"


class BaselineDefinition(DiagnosticModel):
    """A predeclared system configuration, not an observed result."""

    baseline_id: str = Field(pattern=r"^baseline-[a-z0-9-]+$")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    uses_llm: bool
    allows_repairs: bool
    requires_clean_room: bool


class AblationDefinition(DiagnosticModel):
    """A paired treatment that removes one named capability."""

    ablation_id: str = Field(pattern=r"^ablation-[a-z0-9-]+$")
    parent_baseline_id: str = Field(pattern=r"^baseline-[a-z0-9-]+$")
    removed_capability: RemovedCapability
    description: str = Field(min_length=1, max_length=500)


class EvaluationProtocol(DiagnosticModel):
    """Predeclared evaluation design and controls."""

    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    evaluation_set_name: str = Field(min_length=1, max_length=100)
    primary_metric: ComparisonMetric = ComparisonMetric.STATUS_ACCURACY
    baselines: list[BaselineDefinition] = Field(min_length=1, max_length=20)
    ablations: list[AblationDefinition] = Field(max_length=20)
    paired_by_case: bool = True
    fixed_case_order: bool = True

    @model_validator(mode="after")
    def _definitions_are_unique_and_paired(self) -> Self:
        baseline_ids = [item.baseline_id for item in self.baselines]
        if len(set(baseline_ids)) != len(baseline_ids):
            raise ValueError("Baseline IDs must be unique.")
        if "baseline-documented" not in baseline_ids:
            raise ValueError("The documented-only baseline is required.")
        ablation_ids = [item.ablation_id for item in self.ablations]
        if len(set(ablation_ids)) != len(ablation_ids):
            raise ValueError("Ablation IDs must be unique.")
        if any(item.parent_baseline_id not in baseline_ids for item in self.ablations):
            raise ValueError("Every ablation must name a declared parent baseline.")
        if not self.paired_by_case or not self.fixed_case_order:
            raise ValueError(
                "Baseline and ablation comparisons must be paired and ordered."
            )
        return self


class CaseStatusChange(DiagnosticModel):
    """A case whose observed status changed between paired treatments."""

    case_id: str = Field(pattern=r"^eval-[0-9]{3}$")
    baseline_status: ReproductionStatus
    treatment_status: ReproductionStatus


class ComparisonResult(DiagnosticModel):
    """A paired, descriptive metric delta without a causal conclusion."""

    evaluation_set_name: str = Field(min_length=1, max_length=100)
    baseline_id: str = Field(min_length=1, max_length=100)
    treatment_id: str = Field(min_length=1, max_length=100)
    metric: ComparisonMetric
    case_count: int = Field(ge=1)
    baseline_value: float
    treatment_value: float
    absolute_delta: float
    status_changes: list[CaseStatusChange] = Field(max_length=500)


class ComparisonError(ValueError):
    """Raised when two evaluation reports cannot be compared fairly."""


def default_evaluation_protocol() -> EvaluationProtocol:
    """Return the repository's predeclared baseline and ablation protocol."""

    return EvaluationProtocol(
        evaluation_set_name="reproscout-controlled-v1",
        baselines=[
            BaselineDefinition(
                baseline_id="baseline-documented",
                name="Documented-only",
                description="Run the official documented workflow without LLM diagnosis or repairs.",
                uses_llm=False,
                allows_repairs=False,
                requires_clean_room=True,
            ),
            BaselineDefinition(
                baseline_id="baseline-deterministic",
                name="Deterministic-only",
                description="Use deterministic analysis, verification, and bounded controls without LLM calls.",
                uses_llm=False,
                allows_repairs=True,
                requires_clean_room=True,
            ),
            BaselineDefinition(
                baseline_id="baseline-bounded-agent",
                name="Bounded-agent",
                description="Use the provider-independent diagnosis and policy-bounded repair workflow.",
                uses_llm=True,
                allows_repairs=True,
                requires_clean_room=True,
            ),
        ],
        ablations=[
            AblationDefinition(
                ablation_id="ablation-no-llm",
                parent_baseline_id="baseline-bounded-agent",
                removed_capability=RemovedCapability.LLM_DIAGNOSIS,
                description="Disable provider calls while retaining deterministic diagnosis and controls.",
            ),
            AblationDefinition(
                ablation_id="ablation-no-repairs",
                parent_baseline_id="baseline-bounded-agent",
                removed_capability=RemovedCapability.CONTROLLED_REPAIRS,
                description="Disable repair proposals and workspace changes after diagnosis.",
            ),
            AblationDefinition(
                ablation_id="ablation-no-clean-room",
                parent_baseline_id="baseline-bounded-agent",
                removed_capability=RemovedCapability.CLEAN_ROOM,
                description="Omit the independent clean-room rerun while retaining the initial workflow.",
            ),
        ],
    )


def compare_evaluation_reports(
    baseline_id: str,
    treatment_id: str,
    baseline: EvaluationReport,
    treatment: EvaluationReport,
    *,
    metric: ComparisonMetric,
) -> ComparisonResult:
    """Compare paired reports with no tuning, reordering, or causal inference."""

    if baseline.evaluation_set != treatment.evaluation_set:
        raise ComparisonError("Reports use different evaluation sets.")
    baseline_ids = [item.case_id for item in baseline.observations]
    treatment_ids = [item.case_id for item in treatment.observations]
    if baseline_ids != treatment_ids:
        raise ComparisonError("Reports must contain the same cases in the same order.")
    baseline_value = getattr(baseline.metrics, metric.value)
    treatment_value = getattr(treatment.metrics, metric.value)
    if baseline_value is None or treatment_value is None:
        raise ComparisonError(f"Metric {metric.value} is unavailable for comparison.")
    changes = [
        CaseStatusChange(
            case_id=before.case_id,
            baseline_status=before.observed_status,
            treatment_status=after.observed_status,
        )
        for before, after in zip(baseline.observations, treatment.observations)
        if before.observed_status is not after.observed_status
    ]
    return ComparisonResult(
        evaluation_set_name=baseline.evaluation_set,
        baseline_id=baseline_id,
        treatment_id=treatment_id,
        metric=metric,
        case_count=len(baseline.observations),
        baseline_value=baseline_value,
        treatment_value=treatment_value,
        absolute_delta=round(treatment_value - baseline_value, 6),
        status_changes=changes,
    )
