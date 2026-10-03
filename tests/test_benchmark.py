from __future__ import annotations

from reproscout.evaluation import load_benchmark_manifest, run_benchmark


def test_real_repository_benchmark_is_explicitly_unconfigured() -> None:
    manifest = load_benchmark_manifest()
    assert manifest.name == "reproscout-real-python-v0.1"
    assert manifest.repositories == []
    report = run_benchmark(manifest, lambda repository: None)  # type: ignore[arg-type]
    assert report.observations == []
    assert report.false_reproduced_count == 0
