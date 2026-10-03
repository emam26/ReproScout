"""Strict, secret-free projections of the append-only run event stream."""

from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import Field, model_validator

from reproscout.diagnostics.models import DiagnosticModel
from reproscout.state import EventType, Stage
from reproscout.status import ReproductionStatus
from reproscout.verification import VerificationLevel


class StageMetric(DiagnosticModel):
    """Observed time and event count for one lifecycle stage."""

    stage: Stage
    duration_seconds: float = Field(ge=0)
    event_count: int = Field(ge=1)


class TimelineEntry(DiagnosticModel):
    """A bounded human-readable view of one persisted event."""

    sequence: int = Field(ge=1)
    event_type: EventType
    stage: Stage
    timestamp: datetime
    summary: str = Field(min_length=1, max_length=300)


class RunMetricSummary(DiagnosticModel):
    """Machine-queryable metrics derived from one run's real evidence."""

    run_id: str = Field(min_length=1, max_length=200)
    duration_seconds: float = Field(ge=0)
    stage_metrics: list[StageMetric] = Field(max_length=10)
    attempt_count: int = Field(ge=0)
    repair_count: int = Field(ge=0)
    llm_calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    token_usage_complete: bool
    failure_categories: dict[str, int] = Field(max_length=100)
    verification_level: VerificationLevel | None = None
    final_status: ReproductionStatus | None = None
    docker_container_ids: list[str] = Field(max_length=100)
    network_modes: list[str] = Field(max_length=10)

    @model_validator(mode="after")
    def _categories_are_nonnegative(self) -> Self:
        if any(not key or value < 0 for key, value in self.failure_categories.items()):
            raise ValueError("Failure category counts must be non-negative.")
        return self


class RunObservability(DiagnosticModel):
    """The stable timeline and metrics projection for one run."""

    run_id: str = Field(min_length=1, max_length=200)
    metrics: RunMetricSummary
    timeline: list[TimelineEntry] = Field(min_length=1, max_length=100_000)

    @model_validator(mode="after")
    def _timeline_belongs_to_run(self) -> Self:
        if self.metrics.run_id != self.run_id:
            raise ValueError("Observability run IDs must agree.")
        if [entry.sequence for entry in self.timeline] != list(
            range(1, len(self.timeline) + 1)
        ):
            raise ValueError("Timeline sequences must be contiguous and ordered.")
        return self

    def query_timeline(self, *, stage: Stage | None = None) -> list[TimelineEntry]:
        """Return timeline entries, optionally filtered by lifecycle stage."""

        if stage is None:
            return list(self.timeline)
        return [entry for entry in self.timeline if entry.stage is stage]


class ObservabilityIndex:
    """Small in-memory query helper for evaluation without a second log backend."""

    def __init__(self) -> None:
        self._runs: dict[str, RunObservability] = {}

    def add(self, observation: RunObservability) -> None:
        """Add or replace the projection for one run ID."""

        self._runs[observation.run_id] = observation

    def get(self, run_id: str) -> RunObservability | None:
        """Return a projection by run ID, if it has been indexed."""

        return self._runs.get(run_id)

    def query(
        self,
        *,
        final_status: ReproductionStatus | None = None,
        failure_category: str | None = None,
    ) -> list[RunObservability]:
        """Select projections by stable metrics useful to evaluation code."""

        results = list(self._runs.values())
        if final_status is not None:
            results = [
                item for item in results if item.metrics.final_status is final_status
            ]
        if failure_category is not None:
            results = [
                item
                for item in results
                if failure_category in item.metrics.failure_categories
            ]
        return sorted(results, key=lambda item: item.run_id)
