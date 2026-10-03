"""Schema and runner for the pre-v0.1 real-repository benchmark."""

from __future__ import annotations

import json
from collections.abc import Callable
from enum import StrEnum
from importlib.resources import files
from typing import Self

from pydantic import Field, model_validator

from reproscout.diagnostics.models import DiagnosticModel, FailureClass
from reproscout.status import ReproductionStatus
from reproscout.verification import VerificationLevel


class BenchmarkGoal(StrEnum):
    INSTALL = "install"
    TESTS = "tests"
    DEMO = "demo"


class BenchmarkRepository(DiagnosticModel):
    """One public, pinned repository selected for bounded evaluation."""

    repository_id: str = Field(pattern=r"^bench-[a-z0-9-]+$")
    repository_url: str = Field(pattern=r"^https://github\.com/[^/]+/[^/]+$")
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    goal: BenchmarkGoal
    max_runtime_seconds: int = Field(default=600, ge=30, le=3_600)
    selection_notes: str = Field(min_length=1, max_length=1_000)


class BenchmarkManifest(DiagnosticModel):
    """Versioned benchmark selection; an empty manifest is intentionally valid."""

    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    name: str = Field(min_length=1, max_length=100)
    repositories: list[BenchmarkRepository] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _repository_ids_are_unique(self) -> Self:
        ids = [item.repository_id for item in self.repositories]
        if len(ids) != len(set(ids)):
            raise ValueError("Benchmark repository IDs must be unique.")
        return self


class BenchmarkObservation(DiagnosticModel):
    """Observed facts from one real-repository run; no expected label is inferred."""

    repository_id: str
    observed_status: ReproductionStatus
    observed_verification_level: VerificationLevel | None = None
    failure_category: FailureClass | None = None
    clean_room_verified: bool | None = None
    runtime_seconds: float = Field(ge=0)
    attempt_count: int = Field(ge=0)
    llm_call_count: int = Field(ge=0)
    token_usage: int | None = Field(default=None, ge=0)
    false_reproduced: bool | None = None


class BenchmarkReport(DiagnosticModel):
    """Collected benchmark observations, preserving unknown adjudication."""

    benchmark_name: str
    observations: list[BenchmarkObservation] = Field(max_length=20)
    false_reproduced_count: int | None = Field(default=None, ge=0)


BenchmarkAdapter = Callable[[BenchmarkRepository], BenchmarkObservation]


def load_benchmark_manifest() -> BenchmarkManifest:
    """Load only the checked-in benchmark selection, without cloning anything."""

    try:
        raw = (
            files("reproscout.evaluation")
            .joinpath("benchmark_manifest.json")
            .read_text(encoding="utf-8")
        )
        return BenchmarkManifest.model_validate(json.loads(raw))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError("The packaged benchmark manifest is invalid.") from exc


def run_benchmark(
    manifest: BenchmarkManifest,
    adapter: BenchmarkAdapter,
) -> BenchmarkReport:
    """Run exactly the configured pinned cases through a caller-owned adapter."""

    observations: list[BenchmarkObservation] = []
    for repository in manifest.repositories:
        observation = adapter(repository)
        if observation.repository_id != repository.repository_id:
            raise ValueError("Benchmark adapter returned the wrong repository ID.")
        observations.append(observation)
    adjudicated = [item.false_reproduced for item in observations]
    false_count = (
        sum(item is True for item in adjudicated)
        if all(item is not None for item in adjudicated)
        else None
    )
    return BenchmarkReport(
        benchmark_name=manifest.name,
        observations=observations,
        false_reproduced_count=false_count,
    )
