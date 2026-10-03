"""Typed sandbox interfaces and shared configuration models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class SandboxError(RuntimeError):
    """Base error for sandbox setup, execution, and cleanup failures."""


class DockerCliUnavailableError(SandboxError):
    """Raised when no Docker CLI can be found."""


class DockerDaemonUnavailableError(SandboxError):
    """Raised when the Docker CLI cannot reach a daemon."""


class DockerCommandError(SandboxError):
    """Raised when a Docker management command fails."""


class DockerCleanupError(SandboxError):
    """Raised when an owned Docker container cannot be removed."""


class SandboxSecurityError(SandboxError):
    """Raised when a requested sandbox workspace violates safety rules."""


class ResourceLimits(BaseModel):
    """Resource limits applied to a Docker container."""

    memory: str = Field(
        default="512m",
        min_length=2,
        max_length=20,
        pattern=r"^[0-9]+(?:[bkmg]|ki|mi|gi|ti)$",
    )
    cpus: float = Field(default=1.0, gt=0, le=8)
    pids_limit: int = Field(default=128, ge=1, le=4_096)
    command_timeout_seconds: float = Field(default=30.0, gt=0, le=3_600)
    create_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    max_workspace_bytes: int = Field(default=100_000_000, ge=1_024, le=10_000_000_000)
    max_workspace_files: int = Field(default=20_000, ge=1, le=1_000_000)


class SandboxConfig(BaseModel):
    """Configuration for one disposable sandbox."""

    image: str = Field(default="python:3.11-slim", min_length=1)
    workspace_path: Path
    run_id: str = Field(min_length=1)
    network: Literal["bridge", "none"] = "none"
    resource_limits: ResourceLimits = Field(default_factory=ResourceLimits)


class ExecutionResult(BaseModel):
    """Objective result of one command executed inside a sandbox."""

    command: str
    stdout: str
    stderr: str
    exit_code: int | None
    duration: float = Field(ge=0)
    timed_out: bool
    container_id: str | None = None
    cleanup_error: str | None = None


class Sandbox(ABC):
    """Interface implemented by disposable execution backends."""

    @abstractmethod
    def create(self) -> None:
        """Create and start the sandbox."""

    @abstractmethod
    def execute(
        self,
        command: str | Sequence[str],
        *,
        timeout_seconds: float | None = None,
    ) -> ExecutionResult:
        """Execute one command inside the sandbox."""

    @abstractmethod
    def destroy(self) -> None:
        """Destroy only the sandbox resources owned by this instance."""
