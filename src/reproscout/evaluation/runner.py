"""Bounded, deterministic aggregation for system evaluation adapters."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable

from reproscout.status import ReproductionStatus

from .models import EvaluationCase, EvaluationSet
from .results import (
    EvaluationMetrics,
    EvaluationMismatch,
    EvaluationObservation,
    EvaluationReport,
    EvaluationStage,
    StageEvaluationMetric,
)


class EvaluationError(ValueError):
    """Raised when an evaluation adapter returns incomplete or foreign data."""


EvaluationAdapter = Callable[[EvaluationCase], EvaluationObservation]


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def evaluate_observations(
    evaluation_set: EvaluationSet,
    observations: Iterable[EvaluationObservation],
) -> EvaluationReport:
    """Compare observations with contracts and compute stable aggregate metrics."""

    values = list(observations)
    expected_by_id = {case.case_id: case for case in evaluation_set.cases}
    observed_by_id: dict[str, EvaluationObservation] = {}
    for observation in values:
        if observation.case_id not in expected_by_id:
            raise EvaluationError(
                f"Observation is not in the evaluation set: {observation.case_id}"
            )
        if observation.case_id in observed_by_id:
            raise EvaluationError(f"Duplicate observation: {observation.case_id}")
        observed_by_id[observation.case_id] = observation
    if set(observed_by_id) != set(expected_by_id):
        missing = sorted(set(expected_by_id) - set(observed_by_id))
        raise EvaluationError(f"Missing observations: {', '.join(missing)}")

    ordered = [observed_by_id[case.case_id] for case in evaluation_set.cases]
    statuses = list(ReproductionStatus)
    expected_counts = {status.value: 0 for status in statuses}
    observed_counts = {status.value: 0 for status in statuses}
    confusion = {
        expected.value: {observed.value: 0 for observed in statuses}
        for expected in statuses
    }
    mismatches: list[EvaluationMismatch] = []
    failures: Counter[str] = Counter()
    status_matches = 0
    verification_matches = 0
    repair_cases = 0
    repair_successes = 0
    interventions = 0
    total_duration = 0.0
    total_steps = 0
    total_llm_calls = 0
    stage_evaluated: Counter[str] = Counter()
    stage_successes: Counter[str] = Counter()

    for case, observation in zip(evaluation_set.cases, ordered):
        expected_counts[case.expected_status.value] += 1
        observed_counts[observation.observed_status.value] += 1
        confusion[case.expected_status.value][observation.observed_status.value] += 1
        status_match = case.expected_status is observation.observed_status
        verification_match = (
            case.expected_verification_level is observation.observed_verification_level
        )
        status_matches += status_match
        verification_matches += verification_match
        if not status_match or not verification_match:
            mismatches.append(
                EvaluationMismatch(
                    case_id=case.case_id,
                    expected_status=case.expected_status,
                    observed_status=observation.observed_status,
                    expected_verification_level=case.expected_verification_level,
                    observed_verification_level=observation.observed_verification_level,
                    status_mismatch=not status_match,
                    verification_mismatch=not verification_match,
                )
            )
        if observation.failure_category is not None:
            failures[observation.failure_category.value] += 1
        if observation.repair_attempted:
            repair_cases += 1
            repair_successes += observation.repair_succeeded
        interventions += observation.human_intervention
        total_duration += observation.duration_seconds
        total_steps += observation.agent_steps
        total_llm_calls += observation.llm_calls
        for stage, succeeded in observation.stage_outcomes.items():
            if succeeded is not None:
                stage_evaluated[stage] += 1
                stage_successes[stage] += succeeded

    stage_metrics = [
        StageEvaluationMetric(
            stage=stage,
            evaluated_cases=stage_evaluated[stage.value],
            successful_cases=stage_successes[stage.value],
            success_rate=_rate(
                stage_successes[stage.value], stage_evaluated[stage.value]
            ),
        )
        for stage in EvaluationStage
    ]
    metrics = EvaluationMetrics(
        case_count=len(ordered),
        status_accuracy=round(status_matches / len(ordered), 6),
        verification_accuracy=round(verification_matches / len(ordered), 6),
        expected_status_counts=expected_counts,
        observed_status_counts=observed_counts,
        confusion_matrix=confusion,
        failure_category_counts=dict(sorted(failures.items())),
        repair_case_count=repair_cases,
        repair_success_count=repair_successes,
        repair_success_rate=_rate(repair_successes, repair_cases),
        human_intervention_count=interventions,
        human_intervention_rate=round(interventions / len(ordered), 6),
        mean_duration_seconds=round(total_duration / len(ordered), 6),
        total_agent_steps=total_steps,
        total_llm_calls=total_llm_calls,
        stage_metrics=stage_metrics,
    )
    return EvaluationReport(
        evaluation_set=evaluation_set.name,
        observations=ordered,
        metrics=metrics,
        mismatches=mismatches,
    )


class EvaluationRunner:
    """Run each finite case once through a caller-supplied system adapter."""

    def __init__(self, evaluation_set: EvaluationSet, *, max_cases: int = 500) -> None:
        if max_cases < 1 or max_cases > 500:
            raise ValueError("max_cases must be between 1 and 500.")
        if len(evaluation_set.cases) > max_cases:
            raise EvaluationError("Evaluation set exceeds the configured case bound.")
        self.evaluation_set = evaluation_set
        self.max_cases = max_cases

    def run(self, adapter: EvaluationAdapter) -> EvaluationReport:
        """Collect one typed observation per case and aggregate it deterministically."""

        observations: list[EvaluationObservation] = []
        for case in self.evaluation_set.cases:
            observation = adapter(case)
            if not isinstance(observation, EvaluationObservation):
                raise EvaluationError(
                    f"Adapter returned an invalid observation for {case.case_id}."
                )
            observations.append(observation)
        return evaluate_observations(self.evaluation_set, observations)
