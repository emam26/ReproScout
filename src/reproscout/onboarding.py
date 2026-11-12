"""Read-only diagnostics and consent-gated local runtime setup.

The onboarding layer is intentionally small and independent from repository
analysis. It can inspect a host and propose a finite list of trusted package
manager actions, but it never treats an installer exit code as proof that the
Docker sandbox is usable. Readiness is established only by a subsequent
Docker-backed probe.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from reproscout.sandbox import (
    DockerSandbox,
    ResourceLimits,
    SandboxConfig,
    SandboxError,
    resolve_docker_command,
)
from reproscout.security import redact_sensitive_text

_OUTPUT_LIMIT = 1_200
_COMMAND_TIMEOUT = 20.0
_INSTALL_TIMEOUT = 900.0
_WINDOWS_11_BUILD = 22_000
_MACOS_MIN_MAJOR = 13
_SAFE_CONTEXT = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")


class SetupStatus(str, Enum):
    """Terminal state of one local setup attempt."""

    READY = "READY"
    NEEDS_SETUP = "NEEDS_SETUP"
    DECLINED = "DECLINED"
    ADMIN_REQUIRED = "ADMIN_REQUIRED"
    RESTART_REQUIRED = "RESTART_REQUIRED"
    UNSUPPORTED = "UNSUPPORTED"
    UNVERIFIED = "UNVERIFIED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


@dataclass(frozen=True)
class CommandResult:
    """Bounded result from a known, non-shell host command."""

    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error: str | None = None

    @property
    def output(self) -> str:
        return _bounded_output(
            "\n".join(part for part in (self.stdout, self.stderr) if part)
        )


CommandRunner = Callable[[Sequence[str], float], CommandResult]
ReadinessProbe = Callable[[Sequence[str] | None], tuple[bool, str]]


@dataclass(frozen=True)
class HostSnapshot:
    """Safe host facts used to select a supported setup strategy."""

    system: str
    release: str
    version: str
    machine: str
    cpu_count: int | None
    memory_bytes: int | None
    disk_free_bytes: int | None

    @property
    def windows_build(self) -> int | None:
        if self.system != "Windows":
            return None
        values = [int(value) for value in re.findall(r"\d+", self.version)]
        return values[-1] if values else None

    @property
    def macos_major(self) -> int | None:
        if self.system != "Darwin":
            return None
        values = [int(value) for value in re.findall(r"\d+", self.version)]
        return values[0] if values else None

    def as_dict(self) -> dict[str, object]:
        return {
            "system": self.system,
            "release": self.release,
            "version": self.version,
            "architecture": self.machine,
            "cpu_count": self.cpu_count,
            "memory_bytes": self.memory_bytes,
            "disk_free_bytes": self.disk_free_bytes,
            "windows_build": self.windows_build,
            "macos_major": self.macos_major,
        }


@dataclass(frozen=True)
class DockerInspection:
    """Evidence about Docker installation, context, and daemon state."""

    installed: bool
    daemon_running: bool
    command: tuple[str, ...] | None
    server_version: str | None
    context: str | None
    context_verified: bool
    error: str | None
    sandbox_security: dict[str, str]

    @property
    def ready(self) -> bool:
        return (
            self.installed
            and self.daemon_running
            and self.context_verified
            and self.sandbox_security.get("seccomp") == "verified"
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "installed": self.installed,
            "daemon_running": self.daemon_running,
            "available": self.ready,
            "backend": "docker-cli-subprocess",
            "command": list(self.command) if self.command else None,
            "server_version": self.server_version,
            "context": self.context,
            "endpoint": "context-managed" if self.context else None,
            "context_verified": self.context_verified,
            "error": self.error,
            "sandbox_security": dict(self.sandbox_security),
        }


@dataclass(frozen=True)
class SetupAction:
    """One exact, trusted command proposed by the setup workflow."""

    description: str
    command: tuple[str, ...]
    requires_admin: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "description": self.description,
            "command": list(self.command),
            "requires_admin": self.requires_admin,
        }


@dataclass(frozen=True)
class SetupResult:
    """Auditable result of setup planning, installation, and readiness checks."""

    status: SetupStatus
    host: HostSnapshot
    docker: DockerInspection
    actions: tuple[SetupAction, ...] = ()
    completed_actions: tuple[str, ...] = ()
    message: str = ""
    output: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "host": self.host.as_dict(),
            "docker": self.docker.as_dict(),
            "actions": [action.as_dict() for action in self.actions],
            "completed_actions": list(self.completed_actions),
            "message": self.message,
            "output": list(self.output),
        }


def _bounded_output(value: str | None) -> str:
    """Normalize command diagnostics without retaining unbounded host output."""

    if not value:
        return ""
    clean = redact_sensitive_text(value.replace("\x00", "").strip())
    return clean[-_OUTPUT_LIMIT:]


def run_bounded_command(
    command: Sequence[str], timeout_seconds: float
) -> CommandResult:
    """Run a known command without a shell and with bounded diagnostics."""

    try:
        completed = subprocess.run(
            [str(part) for part in command],
            capture_output=True,
            check=False,
            shell=False,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            returncode=None,
            stdout=_bounded_output(_decode(exc.stdout)),
            stderr=_bounded_output(_decode(exc.stderr)),
            timed_out=True,
            error="command timed out",
        )
    except OSError as exc:
        return CommandResult(returncode=None, error=_bounded_output(str(exc)))
    return CommandResult(
        returncode=completed.returncode,
        stdout=_bounded_output(completed.stdout),
        stderr=_bounded_output(completed.stderr),
    )


def _decode(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def snapshot_host() -> HostSnapshot:
    """Collect host facts without invoking a package manager or shell."""

    system = platform.system()
    version = platform.version()
    if system == "Darwin":
        version = platform.mac_ver()[0] or version
    return HostSnapshot(
        system=system,
        release=platform.release(),
        version=version,
        machine=platform.machine(),
        cpu_count=os.cpu_count(),
        memory_bytes=_memory_bytes(),
        disk_free_bytes=_disk_free_bytes(),
    )


def _memory_bytes() -> int | None:
    if hasattr(os, "sysconf"):
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            if isinstance(pages, int) and isinstance(page_size, int):
                return pages * page_size
        except (OSError, ValueError):
            pass
    return None


def _disk_free_bytes() -> int | None:
    try:
        return shutil.disk_usage(Path.cwd()).free
    except OSError:
        return None


def _runtime_compatibility(host: HostSnapshot) -> tuple[str, str]:
    """Return a compatibility state and a user-facing explanation."""

    if host.system == "Linux":
        return "supported", "native Linux/WSL workspace ownership can be checked"
    if host.system == "Windows":
        build = host.windows_build
        if build is None:
            return "unverified", "Windows build could not be determined"
        if build >= _WINDOWS_11_BUILD:
            return (
                "unverified",
                "Docker Desktop workspace mapping requires a smoke check",
            )
        if build >= 10_000:
            return (
                "unverified",
                "Windows 10 requires a maintained compatible Docker Desktop runtime",
            )
        return (
            "unsupported",
            "this Windows build is outside the supported runtime envelope",
        )
    if host.system == "Darwin":
        major = host.macos_major
        if major is None or major < _MACOS_MIN_MAJOR:
            return (
                "unsupported",
                "this macOS version is outside the supported runtime envelope",
            )
        return (
            "unverified",
            "Docker Desktop/Colima workspace mapping requires a smoke check",
        )
    return (
        "unsupported",
        f"automatic Docker onboarding is not defined for {host.system}",
    )


def _security_controls() -> dict[str, str]:
    """Describe controls enforced by the existing DockerSandbox contract."""

    return {
        "cap_drop_all": "configured",
        "no_new_privileges": "configured",
        "read_only_root_filesystem": "configured",
        "resource_limits": "configured",
        "network_isolation": "configured",
        "non_root_workspace_user": "checked_by_sandbox",
        "seccomp": "unverified",
    }


def _virtualization_status(
    host: HostSnapshot,
    *,
    runner: CommandRunner,
) -> dict[str, str]:
    """Inspect only platform-provided virtualization status commands."""

    if host.system == "Linux":
        return {
            "status": "daemon_managed",
            "detail": (
                "Docker and the host kernel provide the detectable runtime boundary."
            ),
        }
    if host.system == "Windows":
        if shutil.which("wsl.exe") is None:
            return {
                "status": "unavailable",
                "detail": "WSL2 status is not detectable because wsl.exe is missing.",
            }
        result = runner(("wsl.exe", "--status"), _COMMAND_TIMEOUT)
        if result.returncode == 0:
            return {
                "status": "detected",
                "detail": result.output or "WSL status returned.",
            }
        return {
            "status": "unverified",
            "detail": _bounded_output(
                result.output or result.error or "WSL status could not be read."
            ),
        }
    if host.system == "Darwin":
        if shutil.which("colima") is None:
            return {
                "status": "unavailable",
                "detail": (
                    "Colima is not installed; Docker Desktop status is daemon-managed."
                ),
            }
        result = runner(("colima", "status"), _COMMAND_TIMEOUT)
        return {
            "status": "detected" if result.returncode == 0 else "stopped",
            "detail": _bounded_output(
                result.output or result.error or "Colima status returned no details."
            ),
        }
    return {
        "status": "unsupported",
        "detail": f"No virtualization probe is defined for {host.system}.",
    }


def inspect_docker(
    host: HostSnapshot | None = None,
    *,
    runner: CommandRunner = run_bounded_command,
    docker_command: Sequence[str] | None = None,
) -> DockerInspection:
    """Inspect Docker without changing contexts or starting services."""

    snapshot = host or snapshot_host()
    if docker_command is None:
        try:
            command = tuple(resolve_docker_command())
        except SandboxError as exc:
            return DockerInspection(
                installed=False,
                daemon_running=False,
                command=None,
                server_version=None,
                context=None,
                context_verified=False,
                error=_bounded_output(str(exc)),
                sandbox_security=_security_controls(),
            )
    else:
        command = tuple(str(part) for part in docker_command)

    info = runner(
        (*command, "info", "--format", "{{.ServerVersion}}"),
        _COMMAND_TIMEOUT,
    )
    context_result = runner((*command, "context", "show"), _COMMAND_TIMEOUT)
    security_result = runner(
        (*command, "info", "--format", "{{json .SecurityOptions}}"),
        _COMMAND_TIMEOUT,
    )
    security = _security_controls()
    if security_result.returncode == 0:
        try:
            options = json.loads(security_result.stdout)
        except json.JSONDecodeError:
            security["seccomp"] = "unverified"
        else:
            security["seccomp"] = (
                "verified"
                if isinstance(options, list)
                and any("seccomp" in str(option).lower() for option in options)
                else "missing"
            )
    elif security_result.error or security_result.output:
        security["seccomp"] = "unverified"
    context = context_result.stdout.strip() if context_result.returncode == 0 else None
    if context and not _SAFE_CONTEXT.fullmatch(context):
        context = None
    if info.returncode != 0:
        if (
            snapshot.system == "Darwin"
            and shutil.which("colima") is not None
            and "--context" not in command
        ):
            return inspect_docker(
                snapshot,
                runner=runner,
                docker_command=(*command, "--context", "colima"),
            )
        detail = info.output or info.error or "Docker daemon did not respond."
        return DockerInspection(
            installed=True,
            daemon_running=False,
            command=command,
            server_version=None,
            context=context,
            context_verified=False,
            error=_bounded_output(detail),
            sandbox_security=security,
        )
    if context is None:
        return DockerInspection(
            installed=True,
            daemon_running=True,
            command=command,
            server_version=_bounded_output(info.stdout) or "unknown",
            context=None,
            context_verified=False,
            error="Docker daemon is reachable but its context could not be verified.",
            sandbox_security=security,
        )
    return DockerInspection(
        installed=True,
        daemon_running=True,
        command=command,
        server_version=_bounded_output(info.stdout) or "unknown",
        context=context,
        context_verified=True,
        error=(
            None
            if security["seccomp"] == "verified"
            else "Docker daemon is reachable but seccomp could not be verified."
        ),
        sandbox_security=security,
    )


def setup_actions(
    host: HostSnapshot, docker: DockerInspection
) -> tuple[SetupAction, ...]:
    """Return only approved, explicit package-manager/runtime actions."""

    if docker.daemon_running:
        return ()
    if host.system == "Windows":
        if host.windows_build is None or host.windows_build < _WINDOWS_11_BUILD:
            return ()
        if docker.installed:
            if docker.command != ("docker",):
                return ()
            return (
                SetupAction(
                    "Start Docker Desktop using its supported CLI",
                    ("docker", "desktop", "start"),
                ),
            )
        if shutil.which("winget") is None:
            return ()
        return (
            SetupAction(
                "Install Docker Desktop from the Windows package manager",
                ("winget", "install", "--id", "Docker.DockerDesktop", "--exact"),
                requires_admin=True,
            ),
        )
    if host.system == "Darwin":
        if host.macos_major is None or host.macos_major < _MACOS_MIN_MAJOR:
            return ()
        if docker.installed and shutil.which("colima") is not None:
            return (SetupAction("Start the Colima runtime", ("colima", "start")),)
        if shutil.which("brew") is None:
            return ()
        return (
            SetupAction(
                "Install Docker CLI and Colima from Homebrew",
                ("brew", "install", "docker", "colima"),
            ),
            SetupAction("Start the Colima runtime", ("colima", "start")),
        )
    return ()


def _failure_status(result: CommandResult) -> SetupStatus:
    detail = f"{result.output} {result.error or ''}".lower()
    if any(marker in detail for marker in ("restart", "reboot", "sign out")):
        return SetupStatus.RESTART_REQUIRED
    if result.returncode in {5, 740, 1603} or any(
        marker in detail
        for marker in ("access is denied", "administrator", "elevation", "permission")
    ):
        return SetupStatus.ADMIN_REQUIRED
    return SetupStatus.FAILED


def _unverified_docker_message(docker: DockerInspection) -> str:
    """Explain why a reachable Docker daemon is not yet verified."""

    reasons: list[str] = []
    if not docker.context_verified:
        reasons.append("Docker context could not be verified")
    seccomp = docker.sandbox_security.get("seccomp", "unverified")
    if seccomp != "verified":
        reasons.append(f"Docker seccomp capability is {seccomp}")
    if not reasons and docker.error:
        reasons.append(docker.error)
    detail = "; ".join(reasons) or "required Docker capabilities are unverified"
    return _bounded_output(
        "Docker sandbox readiness is unverified; the readiness probe was not "
        f"executed: {detail}."
    )


def _default_readiness_probe(command: Sequence[str] | None) -> tuple[bool, str]:
    """Run the existing secure sandbox contract in a disposable workspace."""

    if command is None:
        return False, "Docker command is unavailable."
    try:
        with tempfile.TemporaryDirectory(prefix="reproscout-setup-") as directory:
            sandbox = DockerSandbox(
                SandboxConfig(
                    workspace_path=Path(directory),
                    run_id=f"setup-{uuid.uuid4().hex[:12]}",
                    resource_limits=ResourceLimits(
                        command_timeout_seconds=15.0,
                        create_timeout_seconds=60.0,
                    ),
                ),
                docker_command=command,
            )
            sandbox.create()
            try:
                result = sandbox.execute(
                    ["python", "-c", "import sys; print(sys.executable)"]
                )
            finally:
                sandbox.destroy()
    except SandboxError as exc:
        return False, _bounded_output(str(exc))
    if result.exit_code != 0 or result.timed_out:
        return False, _bounded_output(
            result.stderr or result.stdout or "Sandbox probe failed."
        )
    if "/runtime/venv/bin/python" not in result.stdout:
        return False, "Sandbox probe did not use the managed runtime interpreter."
    return True, "Docker sandbox created, executed, and cleaned up successfully."


def run_setup(
    *,
    host: HostSnapshot | None = None,
    runner: CommandRunner = run_bounded_command,
    readiness_probe: ReadinessProbe = _default_readiness_probe,
    consent: bool = False,
    dry_run: bool = False,
) -> SetupResult:
    """Plan and, only with consent, execute bounded runtime setup."""

    snapshot = host or snapshot_host()
    compatibility, compatibility_message = _runtime_compatibility(snapshot)
    docker = inspect_docker(snapshot, runner=runner)
    actions = setup_actions(snapshot, docker)

    if compatibility == "unsupported":
        return SetupResult(
            SetupStatus.UNSUPPORTED,
            snapshot,
            docker,
            actions,
            message=compatibility_message,
        )
    if docker.daemon_running:
        if dry_run:
            return SetupResult(
                SetupStatus.UNVERIFIED,
                snapshot,
                docker,
                actions,
                message=(
                    "Dry run: Docker was detected; no readiness probe was executed."
                ),
            )
        if not docker.ready:
            return SetupResult(
                SetupStatus.UNVERIFIED,
                snapshot,
                docker,
                actions,
                message=_unverified_docker_message(docker),
            )
        ready, message = readiness_probe(docker.command)
        if not ready and compatibility != "supported":
            message = f"{compatibility_message}: {message}"
        return SetupResult(
            SetupStatus.READY
            if ready
            else (
                SetupStatus.UNVERIFIED
                if compatibility != "supported"
                else SetupStatus.FAILED
            ),
            snapshot,
            docker,
            actions,
            message=message,
        )
    if not actions:
        if snapshot.system == "Linux":
            message = (
                "Install and start Docker using the distribution-supported method."
            )
            status = SetupStatus.NEEDS_SETUP
        elif snapshot.system == "Darwin" and shutil.which("brew") is None:
            message = "Homebrew is required before Colima and Docker can be installed."
            status = SetupStatus.NEEDS_SETUP
        elif snapshot.system == "Windows" and shutil.which("winget") is None:
            message = "winget is required for the supported Docker Desktop setup path."
            status = SetupStatus.NEEDS_SETUP
        else:
            message = compatibility_message
            status = SetupStatus.UNVERIFIED
        return SetupResult(status, snapshot, docker, actions, message=message)
    if dry_run:
        return SetupResult(
            SetupStatus.NEEDS_SETUP,
            snapshot,
            docker,
            actions,
            message="Dry run: no setup command was executed.",
        )
    if not consent:
        return SetupResult(
            SetupStatus.DECLINED,
            snapshot,
            docker,
            actions,
            message="Setup was not authorized; no changes were made.",
        )

    completed: list[str] = []
    output: list[str] = []
    try:
        for action in actions:
            result = runner(action.command, _INSTALL_TIMEOUT)
            if result.output:
                output.append(_bounded_output(result.output))
            if result.returncode != 0:
                return SetupResult(
                    _failure_status(result),
                    snapshot,
                    inspect_docker(snapshot, runner=runner),
                    actions,
                    tuple(completed),
                    message=_bounded_output(
                        result.error or result.output or "Setup command failed."
                    ),
                    output=tuple(output),
                )
            completed.append(action.description)
    except KeyboardInterrupt:
        try:
            interrupted_docker = inspect_docker(snapshot, runner=runner)
        except KeyboardInterrupt:
            interrupted_docker = docker
        return SetupResult(
            SetupStatus.INTERRUPTED,
            snapshot,
            interrupted_docker,
            actions,
            tuple(completed),
            message="Setup interrupted; rerun setup to resume from detected state.",
            output=tuple(output),
        )

    refreshed = inspect_docker(snapshot, runner=runner)
    if not refreshed.daemon_running:
        return SetupResult(
            SetupStatus.RESTART_REQUIRED,
            snapshot,
            refreshed,
            actions,
            tuple(completed),
            message=(
                "Installation completed, but Docker is not reachable. Start Docker "
                "or restart the system, then rerun setup."
            ),
            output=tuple(output),
        )
    if not refreshed.ready:
        return SetupResult(
            SetupStatus.UNVERIFIED,
            snapshot,
            refreshed,
            actions,
            tuple(completed),
            message=_unverified_docker_message(refreshed),
            output=tuple(output),
        )
    ready, message = readiness_probe(refreshed.command)
    if not ready and compatibility != "supported":
        message = f"{compatibility_message}: {message}"
    return SetupResult(
        SetupStatus.READY
        if ready
        else (
            SetupStatus.UNVERIFIED
            if compatibility != "supported"
            else SetupStatus.FAILED
        ),
        snapshot,
        refreshed,
        actions,
        tuple(completed),
        message=message,
        output=tuple(output),
    )


def doctor_report(
    *,
    host: HostSnapshot | None = None,
    runner: CommandRunner = run_bounded_command,
) -> dict[str, object]:
    """Return a serializable, secret-free local capability report."""

    snapshot = host or snapshot_host()
    compatibility, compatibility_message = _runtime_compatibility(snapshot)
    docker = inspect_docker(snapshot, runner=runner)
    return {
        "host": snapshot.as_dict(),
        "runtime_compatibility": compatibility,
        "runtime_message": compatibility_message,
        "virtualization": _virtualization_status(snapshot, runner=runner),
        "docker": docker.as_dict(),
        "sandbox_security": dict(docker.sandbox_security),
        "remediation": _remediation(snapshot, docker, compatibility),
    }


def _remediation(
    host: HostSnapshot,
    docker: DockerInspection,
    compatibility: str,
) -> list[str]:
    if compatibility == "unsupported":
        return ["Use a supported native Linux/WSL, Windows, or macOS runtime."]
    if not docker.installed:
        if host.system == "Windows":
            return ["Run `reproscout setup` to use the supported Docker Desktop path."]
        if host.system == "Darwin":
            return ["Install Homebrew, then run `reproscout setup` for Colima."]
        return ["Install Docker using the distribution-supported method."]
    if not docker.daemon_running:
        return ["Start Docker Desktop/Colima, then rerun `reproscout doctor`."]
    if not docker.context_verified:
        return ["Keep the current Docker context unchanged and rerun the diagnostic."]
    if docker.sandbox_security.get("seccomp") != "verified":
        return [
            (
                "Docker seccomp capability is not verified; inspect the daemon before "
                "running setup."
            )
        ]
    if compatibility != "supported":
        return ["Run `reproscout setup` for a bounded runtime smoke check."]
    return ["Docker is reachable; run `reproscout setup` to verify the sandbox."]


__all__ = [
    "CommandResult",
    "DockerInspection",
    "HostSnapshot",
    "SetupAction",
    "SetupResult",
    "SetupStatus",
    "doctor_report",
    "inspect_docker",
    "run_bounded_command",
    "run_setup",
    "setup_actions",
    "snapshot_host",
]
