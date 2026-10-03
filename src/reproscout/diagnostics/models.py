"""Typed deterministic diagnostic evidence and verification contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DiagnosticModel(BaseModel):
    """Immutable strict base for diagnostic records."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class FailureClass(StrEnum):
    """Deterministic failure taxonomy used before LLM diagnosis."""

    PYTHON_VERSION_MISMATCH = "PYTHON_VERSION_MISMATCH"
    DEPENDENCY_CONFLICT = "DEPENDENCY_CONFLICT"
    MISSING_PACKAGE = "MISSING_PACKAGE"
    IMPORT_ERROR = "IMPORT_ERROR"
    BUILD_FAILURE = "BUILD_FAILURE"
    BUILD_TOOL_MISSING = "BUILD_TOOL_MISSING"
    SYSTEM_LIBRARY_MISSING = "SYSTEM_LIBRARY_MISSING"
    CUDA_MISMATCH = "CUDA_MISMATCH"
    GPU_REQUIRED = "GPU_REQUIRED"
    OUT_OF_MEMORY = "OUT_OF_MEMORY"
    TIMEOUT = "TIMEOUT"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    CHECKPOINT_MISSING = "CHECKPOINT_MISSING"
    DATASET_MISSING = "DATASET_MISSING"
    DEAD_URL = "DEAD_URL"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    NETWORK_FAILURE = "NETWORK_FAILURE"
    PERMISSION_ERROR = "PERMISSION_ERROR"
    CONFIG_ERROR = "CONFIG_ERROR"
    TEST_FAILURE = "TEST_FAILURE"
    UNKNOWN = "UNKNOWN"


class NormalizedFailure(DiagnosticModel):
    """Compact deterministic failure evidence that references the full log."""

    failure_class: FailureClass
    headline: str = Field(min_length=1, max_length=1_000)
    important_lines: list[str] = Field(default_factory=list, max_length=40)
    log_reference: str | None = Field(default=None, max_length=1_000)
    exit_code: int | None = None
    timed_out: bool = False
    raw_character_count: int = Field(ge=0)
    excerpt_character_count: int = Field(ge=0)
    truncated: bool = False


class RequirementRecord(DiagnosticModel):
    """One PEP 508 requirement parsed by ``packaging``."""

    original: str = Field(min_length=1, max_length=2_000)
    name: str = Field(min_length=1, max_length=300)
    canonical_name: str = Field(min_length=1, max_length=300)
    specifier: str = Field(max_length=1_000)
    marker: str | None = Field(default=None, max_length=1_000)
    extras: list[str] = Field(default_factory=list)


class RequirementParseResult(DiagnosticModel):
    """Valid requirements and bounded parse errors from one source."""

    source: str = Field(min_length=1, max_length=1_000)
    requirements: list[RequirementRecord] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list, max_length=100)


class PackageRecord(DiagnosticModel):
    """Package name and version from machine-readable pip output."""

    name: str = Field(min_length=1, max_length=300)
    version: str = Field(min_length=1, max_length=300)
    requested: bool | None = None
    dependencies: list[str] = Field(default_factory=list, max_length=1_000)


class PipReport(DiagnosticModel):
    """Relevant subset of ``pip install --dry-run --report`` output."""

    pip_version: str | None = Field(default=None, max_length=100)
    install: list[PackageRecord] = Field(default_factory=list, max_length=5_000)


class PipInspectReport(DiagnosticModel):
    """Relevant subset of ``python -m pip inspect`` output."""

    pip_version: str | None = Field(default=None, max_length=100)
    installed: list[PackageRecord] = Field(default_factory=list, max_length=5_000)


class PipCheckIssue(DiagnosticModel):
    """One normalized line from ``python -m pip check``."""

    package: str | None = Field(default=None, max_length=300)
    detail: str = Field(min_length=1, max_length=2_000)


class ImportRecord(DiagnosticModel):
    """One top-level import found through Python AST parsing."""

    module: str = Field(min_length=1, max_length=300)
    source_path: str = Field(min_length=1, max_length=1_000)
    line: int = Field(ge=1)


class ImportDependencyFinding(DiagnosticModel):
    """Comparison of an import with declared distribution names."""

    module: str = Field(min_length=1, max_length=300)
    candidate_distributions: list[str] = Field(default_factory=list)
    declared: bool
    heuristic_mapping: bool = False
    evidence_paths: list[str] = Field(default_factory=list)


class EnvironmentPackage(DiagnosticModel):
    """Installed package in the isolated environment."""

    name: str = Field(min_length=1, max_length=300)
    version: str = Field(min_length=1, max_length=300)


class EnvironmentFingerprint(DiagnosticModel):
    """Secret-free facts captured from the Docker environment."""

    repository_commit_sha: str = Field(min_length=1, max_length=200)
    container_image: str = Field(min_length=1, max_length=500)
    container_image_digest: str | None = Field(default=None, max_length=500)
    os_name: str = Field(min_length=1, max_length=300)
    os_release: str = Field(max_length=500)
    architecture: str = Field(min_length=1, max_length=200)
    python_version: str = Field(min_length=1, max_length=200)
    pip_version: str | None = Field(default=None, max_length=200)
    installed_packages: list[EnvironmentPackage] = Field(
        default_factory=list,
        max_length=5_000,
    )
    cpu_allocation: float = Field(gt=0)
    memory_limit: str = Field(min_length=1, max_length=100)
    gpu_visible: bool
    cuda_runtime: str | None = Field(default=None, max_length=300)
    compiler: str | None = Field(default=None, max_length=500)
    environment_variable_names: list[str] = Field(default_factory=list)
    network_mode: str = Field(min_length=1, max_length=50)


class EvidenceKind(StrEnum):
    FAILURE = "FAILURE"
    DEPENDENCY = "DEPENDENCY"
    DOCUMENTATION = "DOCUMENTATION"
    ENVIRONMENT = "ENVIRONMENT"
    SOURCE = "SOURCE"
    PREVIOUS_ATTEMPT = "PREVIOUS_ATTEMPT"
    PREVIOUS_REPAIR = "PREVIOUS_REPAIR"


class DiagnosticEvidenceItem(DiagnosticModel):
    """One bounded item supplied to a later diagnosis layer."""

    reference: str = Field(pattern=r"^[a-z_]+:[0-9]{3}$")
    kind: EvidenceKind
    content: str = Field(min_length=1, max_length=4_000)
    source: str | None = Field(default=None, max_length=1_000)


class DiagnosticContext(DiagnosticModel):
    """Size-bounded context assembled before any LLM call."""

    failure_class: FailureClass
    failure_signature: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence: list[DiagnosticEvidenceItem] = Field(default_factory=list)
    total_characters: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)
    truncated: bool = False

    @model_validator(mode="after")
    def _character_count_matches(self) -> DiagnosticContext:
        actual = sum(len(item.content) for item in self.evidence)
        if actual != self.total_characters:
            raise ValueError("Diagnostic context character count is inconsistent.")
        return self


class AssetKind(StrEnum):
    CHECKPOINT = "CHECKPOINT"
    DATASET = "DATASET"
    EXTERNAL_URL = "EXTERNAL_URL"
    GIT_LFS_POINTER = "GIT_LFS_POINTER"
    GIT_SUBMODULE = "GIT_SUBMODULE"
    DVC = "DVC"


class AssetReference(DiagnosticModel):
    """Repository-local indicator for a future bounded asset operation."""

    kind: AssetKind
    value: str = Field(min_length=1, max_length=2_000)
    source_path: str = Field(min_length=1, max_length=1_000)


class RepositoryAssets(DiagnosticModel):
    """Deterministically detected asset indicators; no downloads are performed."""

    references: list[AssetReference] = Field(default_factory=list)
    scanned_files: int = Field(ge=0)
    truncated: bool = False


class VerificationTargetType(StrEnum):
    ENVIRONMENT_SETUP = "ENVIRONMENT_SETUP"
    INSTALLATION_SUCCEEDS = "INSTALLATION_SUCCEEDS"
    COMMAND_EXITS_SUCCESSFULLY = "COMMAND_EXITS_SUCCESSFULLY"
    TESTS_EXECUTE = "TESTS_EXECUTE"
    ARTIFACT_EXISTS = "ARTIFACT_EXISTS"
    OUTPUT_CONDITION = "OUTPUT_CONDITION"


class VerificationTarget(DiagnosticModel):
    """Objective evidence condition for a later verifier."""

    target_id: str = Field(pattern=r"^verify-[0-9]{3}$")
    target_type: VerificationTargetType
    description: str = Field(min_length=1, max_length=1_000)
    command: str | None = Field(default=None, max_length=2_000)
    artifact_path: str | None = Field(default=None, max_length=1_000)
    artifact_type: str | None = Field(default=None, max_length=20)
    artifact_size_bytes: int | None = Field(default=None, ge=0)
    artifact_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    output_pattern: str | None = Field(default=None, max_length=1_000)
    required: bool = True

    @model_validator(mode="after")
    def _required_fields_match_target(self) -> VerificationTarget:
        if (
            self.target_type is VerificationTargetType.ARTIFACT_EXISTS
            and self.artifact_path is None
        ):
            raise ValueError("Artifact verification requires artifact_path.")
        if (
            self.target_type is VerificationTargetType.ENVIRONMENT_SETUP
            and self.command is not None
        ):
            raise ValueError("Environment verification does not execute a command.")
        if self.artifact_type is not None and self.artifact_path is None:
            raise ValueError("Artifact type verification requires artifact_path.")
        if self.artifact_size_bytes is not None and self.artifact_path is None:
            raise ValueError("Artifact size verification requires artifact_path.")
        if self.artifact_sha256 is not None and self.artifact_path is None:
            raise ValueError("Artifact hash verification requires artifact_path.")
        if self.artifact_type is not None and self.artifact_type not in {
            "file",
            "directory",
            "symlink",
        }:
            raise ValueError("Artifact type must be file, directory, or symlink.")
        if (
            self.target_type is VerificationTargetType.OUTPUT_CONDITION
            and self.output_pattern is None
        ):
            raise ValueError("Output verification requires output_pattern.")
        if (
            self.target_type
            in {
                VerificationTargetType.COMMAND_EXITS_SUCCESSFULLY,
                VerificationTargetType.TESTS_EXECUTE,
                VerificationTargetType.OUTPUT_CONDITION,
            }
            and self.command is None
        ):
            raise ValueError("Command verification requires command.")
        return self


class VerificationContract(DiagnosticModel):
    """Finite success contract without a final reproducibility classification."""

    goal: str = Field(min_length=1, max_length=200)
    targets: list[VerificationTarget] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def _target_ids_are_sequential(self) -> VerificationContract:
        expected = [f"verify-{index:03d}" for index in range(1, len(self.targets) + 1)]
        if [target.target_id for target in self.targets] != expected:
            raise ValueError("Verification target IDs must be sequential.")
        return self
