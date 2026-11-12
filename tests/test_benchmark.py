from __future__ import annotations

import pytest

from reproscout.evaluation import (
    BenchmarkGoal,
    BenchmarkManifest,
    BenchmarkObservation,
    BenchmarkRepository,
    load_benchmark_manifest,
    run_benchmark,
)
from reproscout.status import ReproductionStatus
from reproscout.verification import VerificationLevel


def test_real_repository_benchmark_is_explicitly_unconfigured() -> None:
    manifest = load_benchmark_manifest()
    assert manifest.name == "reproscout-real-python-v0.1"
    assert manifest.repositories == []
    report = run_benchmark(manifest, lambda repository: None)  # type: ignore[arg-type]
    assert report.observations == []
    assert report.false_reproduced_count == 0


def test_benchmark_binds_observation_to_manifest_commit() -> None:
    commit = "a" * 40
    manifest = BenchmarkManifest(
        name="fixture",
        repositories=[
            BenchmarkRepository(
                repository_id="bench-fixture",
                repository_url="https://github.com/example/fixture",
                commit_sha=commit,
                goal=BenchmarkGoal.TESTS,
                selection_notes="bounded fixture",
            )
        ],
    )
    report = run_benchmark(
        manifest,
        lambda repository: BenchmarkObservation(
            repository_id=repository.repository_id,
            observed_status=ReproductionStatus.PARTIAL,
            observed_verification_level=VerificationLevel.L1,
            runtime_seconds=1,
            attempt_count=1,
            llm_call_count=0,
        ),
    )

    assert report.observations[0].source_commit == commit


def test_benchmark_rejects_observation_for_a_different_commit() -> None:
    commit = "a" * 40
    manifest = BenchmarkManifest(
        name="fixture",
        repositories=[
            BenchmarkRepository(
                repository_id="bench-fixture",
                repository_url="https://github.com/example/fixture",
                commit_sha=commit,
                goal=BenchmarkGoal.TESTS,
                selection_notes="bounded fixture",
            )
        ],
    )

    with pytest.raises(ValueError, match="different commit"):
        run_benchmark(
            manifest,
            lambda repository: BenchmarkObservation(
                repository_id=repository.repository_id,
                source_commit="b" * 40,
                observed_status=ReproductionStatus.PARTIAL,
                observed_verification_level=VerificationLevel.L1,
                runtime_seconds=1,
                attempt_count=1,
                llm_call_count=0,
            ),
        )
