from __future__ import annotations

import pytest
from pydantic import ValidationError

from reproscout.evaluation import (
    EvaluationError,
    EvaluationObservation,
    EvaluationRunner,
    EvaluationStage,
    evaluate_observations,
    load_evaluation_set,
)
from reproscout.status import ReproductionStatus


def _observation(case) -> EvaluationObservation:
    return EvaluationObservation(
        case_id=case.case_id,
        observed_status=case.expected_status,
        observed_verification_level=case.expected_verification_level,
        failure_category=case.expected_failure_class,
        repair_attempted=case.repair_expected,
        repair_succeeded=case.name == "minimal-compatibility-repair",
        human_intervention=False,
        agent_steps=2,
        llm_calls=1,
        duration_seconds=0.5,
        stage_outcomes={
            EvaluationStage.INTAKE.value: True,
            EvaluationStage.ANALYZE.value: True,
            EvaluationStage.EXECUTE.value: case.expected_status
            not in {ReproductionStatus.BLOCKED, ReproductionStatus.UNSAFE},
        },
    )


def test_evaluation_runner_computes_metrics_and_preserves_case_order() -> None:
    evaluation_set = load_evaluation_set()
    calls: list[str] = []

    def adapter(case):
        calls.append(case.case_id)
        return _observation(case)

    report = EvaluationRunner(evaluation_set).run(adapter)

    assert calls == [case.case_id for case in evaluation_set.cases]
    assert [item.case_id for item in report.observations] == calls
    assert report.metrics.case_count == 20
    assert report.metrics.status_accuracy == 1
    assert report.metrics.verification_accuracy == 1
    assert report.metrics.repair_case_count == 2
    assert report.metrics.repair_success_count == 1
    assert report.metrics.repair_success_rate == 0.5
    assert report.metrics.total_agent_steps == 40
    assert report.metrics.total_llm_calls == 20
    assert report.metrics.stage_metrics[0].success_rate == 1
    assert report.mismatches == []


def test_evaluation_runner_reports_contract_mismatch() -> None:
    evaluation_set = load_evaluation_set()

    def adapter(case):
        observation = _observation(case)
        if case.case_id == "eval-001":
            return observation.model_copy(
                update={"observed_status": ReproductionStatus.FAILED}
            )
        return observation

    report = EvaluationRunner(evaluation_set).run(adapter)

    assert report.metrics.status_accuracy == 0.95
    assert report.metrics.verification_accuracy == 1
    assert report.metrics.confusion_matrix["REPRODUCED"]["FAILED"] == 1
    assert report.mismatches[0].case_id == "eval-001"
    assert report.mismatches[0].status_mismatch is True


def test_evaluation_runner_rejects_duplicate_or_missing_observations() -> None:
    evaluation_set = load_evaluation_set()
    observations = [_observation(evaluation_set.cases[0])]

    with pytest.raises(EvaluationError, match="Missing observations"):
        evaluate_observations(evaluation_set, observations)

    with pytest.raises(ValidationError):
        EvaluationObservation(
            case_id="eval-001",
            observed_status="REPRODUCED",
            observed_verification_level="L2",
            repair_succeeded=True,
            agent_steps=0,
            llm_calls=0,
            duration_seconds=0,
        )


def test_evaluation_runner_rejects_foreign_adapter_output() -> None:
    evaluation_set = load_evaluation_set()

    with pytest.raises(EvaluationError, match="invalid observation"):
        EvaluationRunner(evaluation_set).run(lambda case: object())
