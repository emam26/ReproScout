"""Typed repository analysis and evidence provenance models."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class EvidenceProvenance(StrEnum):
    """Strength and origin of an analysis claim."""

    DOCUMENTED = "DOCUMENTED"
    DETERMINISTICALLY_DETECTED = "DETERMINISTICALLY_DETECTED"
    LLM_INFERRED = "LLM_INFERRED"


class AnalysisEvidence(BaseModel):
    """Trace one conclusion to repository-local evidence or explicit inference."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: Annotated[str, Field(min_length=1, max_length=100)]
    value: Annotated[str, Field(min_length=1, max_length=2_000)]
    provenance: EvidenceProvenance
    source_path: str | None = Field(default=None, max_length=500)
    detail: str | None = Field(default=None, max_length=2_000)


class RepositoryAnalysis(BaseModel):
    """Focused structured understanding of an intended repository workflow."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    repository: str
    commit_sha: str
    project_type: str = "unknown"
    runtime_language: str = "python"
    python_version_hints: list[str] = Field(default_factory=list)
    package_manager: str | None = None
    dependency_sources: list[str] = Field(default_factory=list)
    install_commands: list[str] = Field(default_factory=list)
    test_commands: list[str] = Field(default_factory=list)
    run_commands: list[str] = Field(default_factory=list)
    entrypoints: list[str] = Field(default_factory=list)
    environment_variables: list[str] = Field(default_factory=list)
    external_assets: list[str] = Field(default_factory=list)
    gpu_required: bool = False
    network_required: bool = False
    likely_execution_target: str | None = None
    evidence: list[AnalysisEvidence] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    context_files: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)


class AnalysisInference(BaseModel):
    """Narrow optional interpretation accepted from an LLM decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_type: str | None = Field(default=None, max_length=100)
    likely_execution_target: str | None = Field(default=None, max_length=500)
    install_commands: list[str] = Field(default_factory=list, max_length=10)
    test_commands: list[str] = Field(default_factory=list, max_length=10)
    run_commands: list[str] = Field(default_factory=list, max_length=10)
    environment_variables: list[str] = Field(default_factory=list, max_length=20)
    external_assets: list[str] = Field(default_factory=list, max_length=20)
    gpu_required: bool | None = None
    network_required: bool | None = None
    confidence: float = Field(default=0.5, ge=0, le=1)
