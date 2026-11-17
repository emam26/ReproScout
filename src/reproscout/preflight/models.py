"""Typed evidence models for the environment-only preflight workflow."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from reproscout.diagnosis import DiagnosisResult
from reproscout.diagnostics import (
    AssetReference,
    EnvironmentFingerprint,
    RequirementRecord,
)
from reproscout.planning import PlannerLimits
from reproscout.sandbox import ResourceLimits


class PreflightStatus(StrEnum):
    """Deterministic readiness outcomes for environment preflight."""

    READY = "READY"
    PARTIAL = "PARTIAL"
    INCOMPATIBLE = "INCOMPATIBLE"
    MISSING_PREREQUISITES = "MISSING_PREREQUISITES"
    INSTALLATION_FAILED = "INSTALLATION_FAILED"
    UNSAFE = "UNSAFE"
    INCOMPLETE = "INCOMPLETE"
    UNVERIFIED = "UNVERIFIED"


class PreflightProfile(StrEnum):
    """Fixed execution policies available to environment preflight callers."""

    DEFAULT = "default"
    EVALUATION = "evaluation"


class PreflightExecutionProfile(BaseModel):
    """Typed, reportable limits selected before a preflight starts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile: PreflightProfile
    planner_limits: PlannerLimits
    resource_limits: ResourceLimits
    enforced_limits: list[str] = Field(default_factory=list, max_length=20)
    best_effort_limits: list[str] = Field(default_factory=list, max_length=20)
    external_infrastructure_limits: list[str] = Field(
        default_factory=list, max_length=20
    )
    unverified_limits: list[str] = Field(default_factory=list, max_length=20)


def resolve_preflight_profile(
    profile: PreflightProfile,
) -> PreflightExecutionProfile:
    """Return an allowlisted profile without accepting caller-controlled limits."""

    resource_limits = ResourceLimits()
    common_external_limits = [
        (
            "A quota-backed runs directory and Docker data root are required to "
            "bound cumulative host clone, workspace, and image-cache storage."
        ),
        (
            "A metered or allowlisted egress proxy is required to bound dependency "
            "download volume."
        ),
    ]
    common_unverified_limits = [
        "ReproScout does not enforce a network download-byte quota.",
        "ReproScout does not enforce a cumulative host or Docker storage quota.",
    ]
    if profile is PreflightProfile.EVALUATION:
        planner_limits = PlannerLimits(
            max_steps=5,
            per_step_timeout_seconds=180,
            overall_timeout_seconds=900,
        )
        return PreflightExecutionProfile(
            profile=profile,
            planner_limits=planner_limits,
            resource_limits=resource_limits,
            enforced_limits=[
                "Preflight planning permits at most five setup-only steps.",
                "Each permitted preflight step is limited to 180 seconds.",
                "The plan execution budget is limited to 900 seconds.",
                "Docker is configured for at most 1 CPU, 512 MiB memory, and 128 PIDs.",
            ],
            best_effort_limits=[
                (
                    "The remaining plan budget is passed to sandbox creation and "
                    "rechecked before every permitted command."
                ),
                (
                    "Docker availability checks, management calls, and forced "
                    "cleanup are independently bounded but can extend wall-clock "
                    "time beyond the 900-second plan budget."
                ),
            ],
            external_infrastructure_limits=common_external_limits,
            unverified_limits=[
                *common_unverified_limits,
                (
                    "A strict end-to-end 900-second wall-clock limit is not "
                    "independently enforceable by the current Docker lifecycle."
                ),
            ],
        )

    planner_limits = PlannerLimits(
        max_steps=30,
        per_step_timeout_seconds=300,
        overall_timeout_seconds=1_800,
    )
    return PreflightExecutionProfile(
        profile=PreflightProfile.DEFAULT,
        planner_limits=planner_limits,
        resource_limits=resource_limits,
        enforced_limits=[
            "Preflight planning permits at most 30 setup-only steps.",
            "Each permitted preflight step is limited to 300 seconds.",
            "The plan execution budget is limited to 1,800 seconds.",
            "Docker is configured for at most 1 CPU, 512 MiB memory, and 128 PIDs.",
        ],
        best_effort_limits=[
            (
                "The remaining plan budget is passed to sandbox creation and "
                "rechecked before every permitted command."
            ),
        ],
        external_infrastructure_limits=common_external_limits,
        unverified_limits=[
            *common_unverified_limits,
            (
                "The plan execution budget does not independently bound all Docker "
                "management and cleanup wall-clock time."
            ),
        ],
    )


class RequirementKind(StrEnum):
    """Categories of setup facts that can affect environment readiness."""

    PYTHON = "PYTHON"
    PACKAGE = "PACKAGE"
    SYSTEM_TOOL = "SYSTEM_TOOL"
    ENVIRONMENT_VARIABLE = "ENVIRONMENT_VARIABLE"
    ASSET = "ASSET"
    GPU = "GPU"
    PACKAGE_MANAGER = "PACKAGE_MANAGER"


class RequirementState(StrEnum):
    """Evidence state for one detected prerequisite."""

    DETECTED = "DETECTED"
    VERIFIED = "VERIFIED"
    MISSING = "MISSING"
    INCOMPATIBLE = "INCOMPATIBLE"
    UNVERIFIED = "UNVERIFIED"


class PreflightRequirement(BaseModel):
    """One prerequisite with explicit provenance and verification state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: RequirementKind
    name: str = Field(min_length=1, max_length=300)
    expected: str | None = Field(default=None, max_length=1_000)
    state: RequirementState
    optional: bool = False
    source_paths: list[str] = Field(default_factory=list, max_length=50)
    observed: str | None = Field(default=None, max_length=1_000)
    detail: str | None = Field(default=None, max_length=2_000)


class PreflightInspection(BaseModel):
    """Deterministic repository-side setup findings before Docker execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dependency_sources: list[str] = Field(default_factory=list, max_length=50)
    package_requirements: list[RequirementRecord] = Field(
        default_factory=list, max_length=2_000
    )
    optional_package_requirements: list[RequirementRecord] = Field(
        default_factory=list, max_length=2_000
    )
    python_constraints: list[str] = Field(default_factory=list, max_length=50)
    system_tools: list[str] = Field(default_factory=list, max_length=100)
    environment_variables: list[str] = Field(default_factory=list, max_length=100)
    assets: list[AssetReference] = Field(default_factory=list, max_length=500)
    requirements: list[PreflightRequirement] = Field(
        default_factory=list, max_length=500
    )
    dependency_parse_errors: list[str] = Field(default_factory=list, max_length=100)
    incompatibilities: list[str] = Field(default_factory=list, max_length=100)
    missing_prerequisites: list[str] = Field(default_factory=list, max_length=100)
    unverified_capabilities: list[str] = Field(default_factory=list, max_length=100)
    unsafe_findings: list[str] = Field(default_factory=list, max_length=100)
    source_files: list[str] = Field(default_factory=list, max_length=100)
    discovery_complete: bool = True
    discovery_warnings: list[str] = Field(default_factory=list, max_length=100)


class PreflightCommandResult(BaseModel):
    """A bounded record of one permitted command and its actual outcome."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    step_id: str = Field(pattern=r"^step-[0-9]{3}$")
    action_type: str = Field(min_length=1, max_length=100)
    command: str | None = Field(default=None, max_length=2_000)
    purpose: str = Field(min_length=1, max_length=1_000)
    outcome: str = Field(pattern=r"^(PASSED|FAILED|NOT_EXECUTED)$")
    exit_code: int | None = None
    timed_out: bool = False
    failure_kind: str | None = Field(default=None, max_length=100)
    stdout: str = Field(default="", max_length=20_000)
    stderr: str = Field(default="", max_length=20_000)
    source_path: str | None = Field(default=None, max_length=500)


class PreflightAssessment(BaseModel):
    """Objective preflight conclusion and the evidence supporting it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: PreflightStatus
    summary: str = Field(min_length=1, max_length=2_000)
    verified_environment: bool = False
    requirements: list[PreflightRequirement] = Field(
        default_factory=list, max_length=500
    )
    commands: list[PreflightCommandResult] = Field(default_factory=list, max_length=100)
    environment: EnvironmentFingerprint | None = None
    verified_facts: list[str] = Field(default_factory=list, max_length=100)
    unresolved_problems: list[str] = Field(default_factory=list, max_length=100)
    unverified_capabilities: list[str] = Field(default_factory=list, max_length=100)
    safe_next_steps: list[str] = Field(default_factory=list, max_length=100)
    diagnosis: DiagnosisResult | None = None


class PreflightArtifactPaths(BaseModel):
    """The three stable handoff artifacts created by one preflight run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_directory: Path
    environment_report: Path
    environment_evidence: Path
    agent_handoff: Path


class PreflightResult(BaseModel):
    """Small public result returned by CLI and service callers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    repository: str
    commit_sha: str = Field(pattern=r"^[0-9a-fA-F]{40}$")
    status: PreflightStatus
    verified_environment: bool
    artifact_paths: PreflightArtifactPaths
