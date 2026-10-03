from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from reproscout.evaluation import (
    EvaluationCase,
    EvaluationCategory,
    EvaluationSet,
    EvaluationSetError,
    load_evaluation_set,
)


def test_packaged_evaluation_set_has_stable_coverage() -> None:
    evaluation_set = load_evaluation_set()

    assert evaluation_set.schema_version == "1.0"
    assert len(evaluation_set.cases) == 20
    assert [case.case_id for case in evaluation_set.cases] == [
        f"eval-{index:03d}" for index in range(1, 21)
    ]
    coverage = evaluation_set.coverage()
    assert coverage[EvaluationCategory.SUCCESS.value] == 4
    assert coverage[EvaluationCategory.REPAIR.value] == 2
    assert coverage[EvaluationCategory.SAFETY.value] == 1
    assert set(coverage) >= {
        EvaluationCategory.DEPENDENCY.value,
        EvaluationCategory.NETWORK.value,
        EvaluationCategory.CLEAN_ROOM.value,
    }
    assert all(
        case.source.value == "CONTROLLED_FIXTURE" for case in evaluation_set.cases
    )


def test_evaluation_set_is_metadata_only_and_does_not_claim_observed_results() -> None:
    evaluation_set = load_evaluation_set()
    serialized = json.dumps(evaluation_set.model_dump(mode="json"), sort_keys=True)

    assert "observed_status" not in serialized
    assert "result" not in serialized.lower()
    assert all(
        case.fixture_path.startswith("tests/fixtures/evaluation/")
        for case in evaluation_set.cases
    )


def test_evaluation_case_rejects_unsafe_fixture_paths() -> None:
    with pytest.raises(ValidationError):
        EvaluationCase(
            case_id="eval-001",
            name="unsafe",
            source="CONTROLLED_FIXTURE",
            fixture_path="../outside",
            category="SUCCESS",
            goal="demo",
            description="Invalid path.",
            expected_status="REPRODUCED",
            expected_verification_level="L2",
            tags=["invalid"],
        )


def test_evaluation_set_rejects_duplicate_fixture_paths() -> None:
    base = load_evaluation_set().model_dump(mode="python")
    base["cases"][1]["fixture_path"] = base["cases"][0]["fixture_path"]

    with pytest.raises(ValidationError):
        EvaluationSet.model_validate(base)


def test_loader_wraps_malformed_packaged_data(monkeypatch) -> None:
    from reproscout.evaluation import catalog

    class BrokenResource:
        def read_text(self, *, encoding: str) -> str:
            assert encoding == "utf-8"
            return "not-json"

    class BrokenPackage:
        def joinpath(self, name: str) -> BrokenResource:
            assert name == "evaluation_set.json"
            return BrokenResource()

    monkeypatch.setattr(catalog, "files", lambda package: BrokenPackage())
    with pytest.raises(EvaluationSetError):
        load_evaluation_set()
