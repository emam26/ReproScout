"""Typed evaluation-set contracts and controlled fixture labels."""

from __future__ import annotations

from enum import StrEnum
from pathlib import PurePosixPath
from typing import Self

from pydantic import Field, field_validator, model_validator

from reproscout.diagnostics.models import DiagnosticModel, FailureClass
from reproscout.status import ReproductionStatus
from reproscout.verification import VerificationLevel


class EvaluationSource(StrEnum):
    """Provenance of a benchmark case."""

    CONTROLLED_FIXTURE = "CONTROLLED_FIXTURE"
    PUBLIC_REPOSITORY = "PUBLIC_REPOSITORY"


class EvaluationCategory(StrEnum):
    """Mutually interpretable failure or success strata for evaluation."""

    SUCCESS = "SUCCESS"
    REPAIR = "REPAIR"
    DEPENDENCY = "DEPENDENCY"
    PYTHON_RUNTIME = "PYTHON_RUNTIME"
    MISSING_ASSET = "MISSING_ASSET"
    NETWORK = "NETWORK"
    CONFIGURATION = "CONFIGURATION"
    TEST_FAILURE = "TEST_FAILURE"
    OUTPUT_FAILURE = "OUTPUT_FAILURE"
    RESOURCE = "RESOURCE"
    SAFETY = "SAFETY"
    CLEAN_ROOM = "CLEAN_ROOM"
    DOCUMENTATION = "DOCUMENTATION"


class EvaluationCase(DiagnosticModel):
    """One frozen benchmark contract, not an observed run result."""

    case_id: str = Field(pattern=r"^eval-[0-9]{3}$")
    name: str = Field(min_length=1, max_length=100)
    source: EvaluationSource
    fixture_path: str = Field(min_length=1, max_length=300)
    category: EvaluationCategory
    goal: str = Field(min_length=1, max_length=50)
    description: str = Field(min_length=1, max_length=500)
    expected_status: ReproductionStatus
    expected_verification_level: VerificationLevel
    expected_failure_class: FailureClass | None = None
    network_required: bool = False
    repair_expected: bool = False
    tags: list[str] = Field(min_length=1, max_length=20)

    @field_validator("fixture_path")
    @classmethod
    def _fixture_path_is_relative(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        path = PurePosixPath(normalized)
        if (
            path.is_absolute()
            or ".." in path.parts
            or not normalized
            or normalized.startswith("./")
        ):
            raise ValueError("fixture_path must be a normalized relative path.")
        return normalized

    @field_validator("tags")
    @classmethod
    def _tags_are_unique(cls, value: list[str]) -> list[str]:
        if any(not tag.strip() for tag in value):
            raise ValueError("Evaluation tags cannot be empty.")
        if len(set(value)) != len(value):
            raise ValueError("Evaluation tags must be unique.")
        return value

    @model_validator(mode="after")
    def _labels_are_consistent(self) -> Self:
        if (
            self.source is EvaluationSource.CONTROLLED_FIXTURE
            and not self.fixture_path.startswith("tests/fixtures/evaluation/")
        ):
            raise ValueError(
                "Controlled fixtures must live under the evaluation fixture root."
            )
        if (
            self.category is EvaluationCategory.SUCCESS
            and self.expected_status is not ReproductionStatus.REPRODUCED
        ):
            raise ValueError("SUCCESS cases must expect REPRODUCED.")
        if (
            self.category is EvaluationCategory.SAFETY
            and self.expected_status is not ReproductionStatus.UNSAFE
        ):
            raise ValueError("SAFETY cases must expect UNSAFE.")
        if self.category is EvaluationCategory.REPAIR and not self.repair_expected:
            raise ValueError("REPAIR cases must require a repair expectation.")
        return self


class EvaluationSet(DiagnosticModel):
    """Versioned case collection with deterministic identity and coverage."""

    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    cases: list[EvaluationCase] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _cases_are_sequential_and_unique(self) -> Self:
        expected_ids = [f"eval-{index:03d}" for index in range(1, len(self.cases) + 1)]
        actual_ids = [case.case_id for case in self.cases]
        if actual_ids != expected_ids:
            raise ValueError("Evaluation case IDs must be sequential and ordered.")
        if len({case.fixture_path for case in self.cases}) != len(self.cases):
            raise ValueError("Evaluation fixture paths must be unique.")
        return self

    def coverage(self) -> dict[str, int]:
        """Return deterministic category counts for benchmark auditing."""

        counts = {category.value: 0 for category in EvaluationCategory}
        for case in self.cases:
            counts[case.category.value] += 1
        return {key: value for key, value in counts.items() if value}
