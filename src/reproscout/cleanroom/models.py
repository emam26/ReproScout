"""Typed clean-room recipe, limits, and objective result."""

from __future__ import annotations

from pydantic import Field

from reproscout.diagnostics.models import DiagnosticModel
from reproscout.execution import ExecutionRunResult
from reproscout.planning import ReproductionPlan
from reproscout.status import ReproductionStatusResult
from reproscout.verification import VerificationResult


class CleanRoomLimits(DiagnosticModel):
    """Bounds for copying a source workspace into a fresh run."""

    max_files: int = Field(default=20_000, ge=1, le=1_000_000)
    max_bytes: int = Field(default=100_000_000, ge=1_024, le=10_000_000_000)
    max_patch_characters: int = Field(default=4_000, ge=100, le=1_000_000)


class CleanRoomRecipe(DiagnosticModel):
    """Recipe derived from a plan and optional real repair diff."""

    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    repository: str = Field(min_length=1, max_length=500)
    repository_url: str = Field(min_length=1, max_length=2_000)
    commit_sha: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=500)
    plan: ReproductionPlan
    recipe_commands: list[str] = Field(min_length=1, max_length=100)
    patches_diff: str | None = Field(default=None, max_length=1_000_000)


class CleanRoomResult(DiagnosticModel):
    """Evidence from a new run, separate from the repaired source attempt."""

    clean_run_id: str
    clean_workspace: str
    execution: ExecutionRunResult
    verification: VerificationResult
    status: ReproductionStatusResult
