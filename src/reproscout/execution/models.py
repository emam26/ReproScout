"""Structured results for finite Docker plan execution."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ExecutionFailureKind(StrEnum):
    """Deterministically distinguish initial execution failure classes."""

    COMMAND_NONZERO = "COMMAND_NONZERO"
    TIMEOUT = "TIMEOUT"
    SANDBOX_FAILURE = "SANDBOX_FAILURE"
    DEPENDENCY_INSTALLATION_FAILURE = "DEPENDENCY_INSTALLATION_FAILURE"
    NETWORK_FAILURE = "NETWORK_FAILURE"
    RESOURCE_LIMIT_FAILURE = "RESOURCE_LIMIT_FAILURE"
    PREREQUISITE_UNAVAILABLE = "PREREQUISITE_UNAVAILABLE"


class StepExecutionResult(BaseModel):
    """Bounded objective evidence for one attempted plan step."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    step_id: str
    attempt_number: int = Field(ge=1)
    action_type: str
    command: str | None
    working_directory: str
    started_at: datetime
    finished_at: datetime
    duration: float = Field(ge=0)
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    container_id: str | None = None
    failure_kind: ExecutionFailureKind | None = None


class RunArtifactPaths(BaseModel):
    """Artifacts produced by the initial execution engine."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_directory: str
    commands_jsonl: str
    events_jsonl: str
    setup_log: str
    execution_log: str
    patches_diff: str
    workspace: str


class ExecutionRunResult(BaseModel):
    """Workflow result, explicitly not a final reproducibility verdict."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    workflow_succeeded: bool
    failure_kind: ExecutionFailureKind | None = None
    failed_step_id: str | None = None
    steps: list[StepExecutionResult]
    started_at: datetime
    finished_at: datetime
    artifacts: RunArtifactPaths


class ExecutionLimits(BaseModel):
    """Output and artifact bounds independent of sandbox resource limits."""

    model_config = ConfigDict(frozen=True)

    max_persisted_output_chars: int = Field(default=20_000, ge=100, le=1_000_000)
    max_log_output_chars: int = Field(default=100_000, ge=100, le=5_000_000)
