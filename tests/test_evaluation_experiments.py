from __future__ import annotations

import pytest
from pydantic import ValidationError

from reproscout.evaluation import (
    ComparisonError,
    ComparisonMetric,
    EvaluationObservation,
    EvaluationProtocol,
    EvaluationRunner,
    compare_evaluation_reports,
    default_evaluation_protocol,
    load_evaluation_set,
)
from reproscout.status import ReproductionStatus


def _report(status_override: str | None = None):
    evaluation_set = load_evaluation_set()

    def adapter(case):
        status = (
            ReproductionStatus(status_override)
            if case.case_id == "eval-001" and status_override is not None
            else case.expected_status
        )
        return EvaluationObservation(
            case_id=case.case_id,
            observed_status=status,
            observed_verification_level=case.expected_verification_level,
            repair_attempted=case.repair_expected,
            repair_succeeded=False,
            agent_steps=1,
            llm_calls=0,
            duration_seconds=1,
        )

    return EvaluationRunner(evaluation_set).run(adapter)


def test_default_protocol_declares_controls_and_single_capability_ablations() -> None:
    protocol = default_evaluation_protocol()

    assert protocol.primary_metric is ComparisonMetric.STATUS_ACCURACY
    assert [item.baseline_id for item in protocol.baselines] == [
        "baseline-documented",
        "baseline-deterministic",
        "baseline-bounded-agent",
    ]
    assert {item.parent_baseline_id for item in protocol.ablations} == {
        "baseline-bounded-agent"
    }
    assert len({item.removed_capability for item in protocol.ablations}) == 3


def test_comparison_is_paired_and_reports_case_status_changes() -> None:
    baseline = _report()
    treatment = _report("FAILED")

    result = compare_evaluation_reports(
        "baseline-bounded-agent",
        "ablation-no-repairs",
        baseline,
        treatment,
        metric=ComparisonMetric.STATUS_ACCURACY,
    )

    assert result.case_count == 20
    assert result.baseline_value == 1
    assert result.treatment_value == 0.95
    assert result.absolute_delta == -0.05
    assert [
        (item.case_id, item.treatment_status) for item in result.status_changes
    ] == [("eval-001", ReproductionStatus.FAILED)]


def test_comparison_rejects_different_case_order_or_metric_without_value() -> None:
    baseline = _report()
    treatment = _report()
    treatment = treatment.model_copy(update={"evaluation_set": "other-set"})

    with pytest.raises(ComparisonError, match="different evaluation sets"):
        compare_evaluation_reports(
            "baseline-documented",
            "ablation-no-llm",
            baseline,
            treatment,
            metric=ComparisonMetric.STATUS_ACCURACY,
        )

    with pytest.raises(ValidationError):
        EvaluationProtocol(
            evaluation_set_name="fixture",
            baselines=[],
            ablations=[],
        )
