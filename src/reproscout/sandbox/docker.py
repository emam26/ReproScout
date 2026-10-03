"""Docker-backed disposable sandbox implementation."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Self

from pydantic import BaseModel

from .base import (
    DockerCleanupError,
    DockerCliUnavailableError,
    DockerCommandError,
    DockerDaemonUnavailableError,
    ExecutionResult,
    Sandbox,
    SandboxConfig,
    SandboxSecurityError,
)


class DockerAvailability(BaseModel):
    """Verified Docker CLI and daemon information."""

    command: list[str]
    server_version: str


_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


def resolve_docker_command(
    preferred: Sequence[str] | None = None,
) -> tuple[str, ...]:
    """Resolve a native Docker CLI or the WSL Docker CLI on Windows."""

    if preferred:
        return tuple(str(part) for part in preferred)
    if shutil.which("docker"):
        return ("docker",)
    if os.name == "nt" and shutil.which("wsl.exe"):
        return ("wsl.exe", "-e", "docker")
    raise DockerCliUnavailableError(
        "Docker CLI was not found. Install Docker and ensure docker is on PATH."
    )


def _decode_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _clean_docker_output(output: str) -> str:
    message = output.strip().replace("\x00", "")
    return message[-800:] if message else "no additional Docker output"


def _run_docker_command(
    command: Sequence[str],
    args: Sequence[str],
    *,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    """Run Docker without a shell and preserve command output."""

    try:
        return subprocess.run(
            [*command, *args],
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise DockerCliUnavailableError(
            "Docker CLI was not found while executing the requested operation."
        ) from exc


def check_docker_available(
    docker_command: Sequence[str] | None = None,
) -> DockerAvailability:
    """Verify that the Docker CLI and daemon are usable."""

    command = resolve_docker_command(docker_command)
    try:
        result = _run_docker_command(
            command,
            ["info", "--format", "{{.ServerVersion}}"],
            timeout_seconds=15,
        )
    except subprocess.TimeoutExpired as exc:
        raise DockerDaemonUnavailableError(
            "Docker daemon did not respond within 15 seconds."
        ) from exc
    if result.returncode != 0:
        output = result.stderr or result.stdout
        raise DockerDaemonUnavailableError(
            f"Docker daemon is unavailable: {_clean_docker_output(output)}"
        )
    return DockerAvailability(
        command=list(command),
        server_version=result.stdout.strip(),
    )


class DockerSandbox(Sandbox):
    """A disposable, resource-limited Docker container."""

    def __init__(
        self,
        config: SandboxConfig,
        *,
        docker_command: Sequence[str] | None = None,
    ) -> None:
        self.config = config
        self.docker_command = resolve_docker_command(docker_command)
        self.container_id: str | None = None
        self.container_name = self._container_name_for(config.run_id)

    @staticmethod
    def _container_name_for(run_id: str) -> str:
        if not _SAFE_RUN_ID.fullmatch(run_id):
            raise SandboxSecurityError(
                "run_id must contain only letters, numbers, '.', '_', or '-'."
            )
        return f"reproscout-{run_id}"

    @property
    def labels(self) -> dict[str, str]:
        """Return labels used to prove ownership of the container."""

        return {
            "reproscout.managed": "true",
            "reproscout.run_id": self.config.run_id,
        }

    @property
    def is_wsl_command(self) -> bool:
        return any(Path(part).name.lower() == "wsl.exe" for part in self.docker_command)

    def _docker_workspace_path(self) -> str:
        workspace = self.config.workspace_path.resolve()
        rendered = str(workspace).replace("\\", "/")
        if self.is_wsl_command and len(rendered) >= 2 and rendered[1] == ":":
            return f"/mnt/{rendered[0].lower()}{rendered[2:]}"
        return rendered

    def _build_run_args(self) -> list[str]:
        limits = self.config.resource_limits
        args = [
            "run",
            "--detach",
            "--name",
            self.container_name,
            "--label",
            "reproscout.managed=true",
            "--label",
            f"reproscout.run_id={self.config.run_id}",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            f"--network={self.config.network}",
            f"--memory={limits.memory}",
            f"--cpus={limits.cpus:g}",
            f"--pids-limit={limits.pids_limit}",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=64m",
            "--mount",
            (f"type=bind,source={self._docker_workspace_path()},target=/workspace"),
            "--workdir",
            "/workspace",
            self.config.image,
            "python",
            "-c",
            "import time; time.sleep(31536000)",
        ]
        return args

    def _validate_workspace(self) -> None:
        workspace_input = self.config.workspace_path.expanduser()
        if workspace_input.is_symlink():
            raise SandboxSecurityError("Workspace cannot be a symlink.")
        workspace = workspace_input.resolve()
        if not workspace.is_dir():
            raise SandboxSecurityError(
                f"Workspace must be a real directory: {workspace}"
            )
        file_count = 0
        total_bytes = 0
        for current, directories, files in os.walk(
            workspace,
            topdown=True,
            followlinks=False,
        ):
            current_path = Path(current)
            for directory in directories:
                directory_path = current_path / directory
                if directory_path.is_symlink():
                    raise SandboxSecurityError(
                        "Workspace cannot contain symlinked directories."
                    )
                if directory.lower() in {".ssh", ".aws", ".azure", ".gcloud"}:
                    raise SandboxSecurityError(
                        "Workspace contains a credentials directory that cannot be mounted."
                    )
            for filename in files:
                file_path = current_path / filename
                if file_path.is_symlink():
                    raise SandboxSecurityError(
                        "Workspace cannot contain symlinked files."
                    )
                if filename.lower() in {
                    ".env",
                    "id_rsa",
                    "id_ed25519",
                    "credentials",
                }:
                    raise SandboxSecurityError(
                        "Workspace contains a credential or .env file that cannot be mounted."
                    )
                try:
                    file_size = file_path.stat().st_size
                except OSError as exc:
                    raise SandboxSecurityError(
                        "Workspace file metadata could not be inspected safely."
                    ) from exc
                file_count += 1
                total_bytes += file_size
                if file_count > self.config.resource_limits.max_workspace_files:
                    raise SandboxSecurityError(
                        "Workspace exceeds the file-count limit."
                    )
                if total_bytes > self.config.resource_limits.max_workspace_bytes:
                    raise SandboxSecurityError("Workspace exceeds the byte-size limit.")

    def _invoke(
        self,
        args: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[str]:
        return _run_docker_command(
            self.docker_command,
            args,
            timeout_seconds=timeout_seconds,
        )

    def _raise_for_management_failure(
        self,
        result: subprocess.CompletedProcess[str],
        action: str,
    ) -> None:
        if result.returncode != 0:
            output = result.stderr or result.stdout
            raise DockerCommandError(
                f"Docker {action} failed with exit code {result.returncode}: "
                f"{_clean_docker_output(output)}"
            )

    def create(self) -> None:
        """Create one labeled container for this sandbox."""

        if self.container_id is not None:
            return
        self._validate_workspace()
        check_docker_available(self.docker_command)
        try:
            result = self._invoke(
                self._build_run_args(),
                timeout_seconds=self.config.resource_limits.create_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            self._remove_by_name()
            raise DockerCommandError("Docker container creation timed out.") from exc
        if result.returncode != 0:
            self._remove_by_name()
            self._raise_for_management_failure(result, "container creation")
        container_ids = result.stdout.strip().splitlines()
        container_id = container_ids[-1] if container_ids else ""
        if not container_id:
            self._remove_by_name()
            raise DockerCommandError("Docker returned no container ID after creation.")
        self.container_id = container_id

    def execute(
        self,
        command: str | Sequence[str],
        *,
        timeout_seconds: float | None = None,
    ) -> ExecutionResult:
        """Execute a command inside the owned container."""

        if self.container_id is None:
            raise DockerCommandError("Sandbox must be created before execution.")
        if isinstance(command, str):
            command_text = command
            command_args = ["sh", "-c", command]
        else:
            command_args = [str(part) for part in command]
            if not command_args:
                raise DockerCommandError("Sandbox command cannot be empty.")
            command_text = shlex.join(command_args)

        timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else self.config.resource_limits.command_timeout_seconds
        )
        started = time.monotonic()
        try:
            result = self._invoke(
                ["exec", self.container_id, *command_args],
                timeout_seconds=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = _decode_output(exc.stdout)
            stderr = _decode_output(exc.stderr)
            container_id = self.container_id
            cleanup_error: str | None = None
            try:
                self.destroy()
            except DockerCleanupError as cleanup_exc:
                cleanup_error = str(cleanup_exc)
            return ExecutionResult(
                command=command_text,
                stdout=stdout,
                stderr=stderr,
                exit_code=124,
                duration=time.monotonic() - started,
                timed_out=True,
                container_id=container_id,
                cleanup_error=cleanup_error,
            )

        return ExecutionResult(
            command=command_text,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
            duration=time.monotonic() - started,
            timed_out=False,
            container_id=self.container_id,
        )

    def _remove_by_name(self) -> None:
        try:
            result = self._invoke(
                ["rm", "--force", self.container_name],
                timeout_seconds=15,
            )
        except (DockerCliUnavailableError, subprocess.TimeoutExpired):
            return
        if result.returncode != 0 and "No such container" not in (
            result.stderr or result.stdout
        ):
            return

    def destroy(self) -> None:
        """Remove exactly this sandbox's container, if it exists."""

        if self.container_id is None:
            return
        container_id = self.container_id
        try:
            result = self._invoke(
                ["rm", "--force", container_id],
                timeout_seconds=15,
            )
        except subprocess.TimeoutExpired as exc:
            raise DockerCleanupError(
                f"Timed out removing owned container {container_id}."
            ) from exc
        if result.returncode != 0 and "No such container" not in (
            result.stderr or result.stdout
        ):
            output = result.stderr or result.stdout
            raise DockerCleanupError(
                f"Could not remove owned container {container_id}: "
                f"{_clean_docker_output(output)}"
            )
        self.container_id = None

    def __enter__(self) -> Self:
        self.create()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.destroy()
