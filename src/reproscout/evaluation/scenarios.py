"""Explicit v1 scenario coverage metadata without fabricated outcomes."""

from __future__ import annotations

import json
from enum import StrEnum
from importlib.resources import files
from typing import Self

from pydantic import Field, model_validator

from reproscout.diagnostics.models import DiagnosticModel


class ScenarioRunState(StrEnum):
    """String states for owner-run scenario execution records."""

    NOT_RUN = "NOT_RUN"
    PASSED = "PASSED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"


class ScenarioDefinition(DiagnosticModel):
    """One required v1 scenario and its current controlled-test linkage."""

    scenario_id: str = Field(pattern=r"^S[0-9]{2}$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    case_id: str | None = Field(default=None, pattern=r"^eval-[0-9]{3}$")
    state: ScenarioRunState = ScenarioRunState.NOT_RUN
    notes: str = Field(default="", max_length=500)


class ScenarioMatrix(DiagnosticModel):
    """The complete v1 matrix, intentionally separate from observations."""

    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    name: str = Field(min_length=1, max_length=100)
    scenarios: list[ScenarioDefinition] = Field(min_length=20, max_length=20)

    @model_validator(mode="after")
    def _scenarios_are_ordered(self) -> Self:
        expected = [f"S{index:02d}" for index in range(1, 21)]
        if [item.scenario_id for item in self.scenarios] != expected:
            raise ValueError("Scenario IDs must be ordered from S01 through S20.")
        return self


def load_scenario_matrix() -> ScenarioMatrix:
    """Load the v1 matrix without executing a fixture or repository."""

    try:
        raw = (
            files("reproscout.evaluation")
            .joinpath("scenario_matrix.json")
            .read_text(encoding="utf-8")
        )
        return ScenarioMatrix.model_validate(json.loads(raw))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError("The packaged scenario matrix is invalid.") from exc
