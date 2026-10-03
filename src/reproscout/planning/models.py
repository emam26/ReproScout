"""Finite, inspectable reproduction-plan models."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from reproscout.analysis import AnalysisEvidence, ResolvedEnvironment


class PlanActionType(StrEnum):
    """Supported non-autonomous plan step categories."""

    PREPARE_ENVIRONMENT = "PREPARE_ENVIRONMENT"
    INSTALL_DEPENDENCY = "INSTALL_DEPENDENCY"
    PREPARE_ASSET = "PREPARE_ASSET"
    RUN_SETUP = "RUN_SETUP"
    RUN_DEMO = "RUN_DEMO"
    RUN_TESTS = "RUN_TESTS"
    VERIFY_BASIC_EXECUTION = "VERIFY_BASIC_EXECUTION"


class RiskLevel(StrEnum):
    """Conservative execution risk classification."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class PlanBaseline(StrEnum):
    """Origin of the first-attempt procedure."""

    OFFICIAL_DOCUMENTATION = "OFFICIAL_DOCUMENTATION"
    STRUCTURED_METADATA = "STRUCTURED_METADATA"
    EXPLICIT_INFERENCE = "EXPLICIT_INFERENCE"


class PlanProvenance(StrEnum):
    """Origin of a selected command or prerequisite."""

    DOCUMENTED = "DOCUMENTED"
    INFERRED = "INFERRED"
    USER_SPECIFIED = "USER_SPECIFIED"
    TOOL_GENERATED = "TOOL_GENERATED"


class PlanStep(BaseModel):
    """One ordered, bounded action; commandless steps represent prerequisites."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    step_id: str = Field(pattern=r"^step-[0-9]{3}$")
    action_type: PlanActionType
    command: str | None = Field(default=None, max_length=2_000)
    working_directory: str = Field(default=".", min_length=1, max_length=500)
    purpose: str = Field(min_length=1, max_length=1_000)
    evidence: list[AnalysisEvidence] = Field(default_factory=list)
    timeout_seconds: float = Field(gt=0, le=3_600)
    network_required: bool = False
    expected_outcome: str = Field(min_length=1, max_length=1_000)
    risk: RiskLevel = RiskLevel.LOW
    provenance: PlanProvenance = PlanProvenance.TOOL_GENERATED
    source_path: str | None = Field(default=None, max_length=500)
    source_location: str | None = Field(default=None, max_length=200)
    attempt_number: int = Field(default=1, ge=1)


class ReproductionPlan(BaseModel):
    """Finite first-attempt plan that contains no execution results."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    repository: str
    repository_url: str | None = None
    commit_sha: str
    goal: str
    requested_goal: str = "auto"
    baseline: PlanBaseline
    steps: list[PlanStep]
    required_milestones: list[str] = Field(default_factory=list, max_length=10)
    resolved_environment: ResolvedEnvironment | None = None
    overall_timeout_seconds: float = Field(gt=0, le=86_400)

    @model_validator(mode="after")
    def _step_ids_must_be_ordered_and_unique(self) -> ReproductionPlan:
        expected = [f"step-{index:03d}" for index in range(1, len(self.steps) + 1)]
        if [step.step_id for step in self.steps] != expected:
            raise ValueError("Plan step IDs must be unique and sequential.")
        return self


class PlannerLimits(BaseModel):
    """Hard bounds applied before a plan can reach the execution layer."""

    model_config = ConfigDict(frozen=True)

    max_steps: int = Field(default=20, ge=1, le=100)
    max_command_length: int = Field(default=1_000, ge=16, le=10_000)
    per_step_timeout_seconds: float = Field(default=300, gt=0, le=3_600)
    overall_timeout_seconds: float = Field(default=1_800, gt=0, le=86_400)
