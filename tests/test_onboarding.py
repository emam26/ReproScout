from __future__ import annotations

from collections.abc import Sequence

import pytest

from reproscout.onboarding import (
    CommandResult,
    HostSnapshot,
    SetupStatus,
    doctor_report,
    inspect_docker,
    run_setup,
)
from reproscout.sandbox import DockerCliUnavailableError


def _host(system: str, *, version: str) -> HostSnapshot:
    return HostSnapshot(
        system=system,
        release="test-release",
        version=version,
        machine="x86_64",
        cpu_count=8,
        memory_bytes=16 * 1024**3,
        disk_free_bytes=100 * 1024**3,
    )


class DockerRunner:
    def __init__(
        self,
        *,
        running: bool = True,
        security: str = '["name=seccomp"]',
        context_verified: bool = True,
    ):
        self.running = running
        self.security = security
        self.context_verified = context_verified
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, command: Sequence[str], timeout: float) -> CommandResult:
        del timeout
        normalized = tuple(command)
        self.calls.append(normalized)
        if normalized[0] == "winget":
            self.running = True
            return CommandResult(0)
        if normalized[0] == "brew":
            return CommandResult(0)
        if normalized[0] == "colima":
            self.running = True
            return CommandResult(0)
        if normalized[:3] == ("docker", "desktop", "start"):
            self.running = True
            return CommandResult(0)
        if normalized[1:3] == ("context", "show"):
            if not self.context_verified:
                return CommandResult(1, stderr="Docker context could not be read")
            return CommandResult(0, "default\n")
        if normalized[-1] == "{{json .SecurityOptions}}":
            return CommandResult(0, self.security)
        if normalized[1:3] == ("info", "--format"):
            if self.running:
                return CommandResult(0, "27.0.1\n")
            return CommandResult(1, stderr="Cannot connect to the Docker daemon")
        return CommandResult(0)


def _missing_docker() -> tuple[str, ...]:
    raise DockerCliUnavailableError("missing")


def test_doctor_reports_context_security_and_host_capacity(monkeypatch) -> None:
    runner = DockerRunner()
    monkeypatch.setattr(
        "reproscout.onboarding.resolve_docker_command", lambda: ("docker",)
    )

    report = doctor_report(host=_host("Linux", version="6.8.0"), runner=runner)

    assert report["runtime_compatibility"] == "supported"
    docker = report["docker"]
    assert isinstance(docker, dict)
    assert docker["available"] is True
    assert docker["context"] == "default"
    assert docker["sandbox_security"]["seccomp"] == "verified"
    assert report["host"]["cpu_count"] == 8  # type: ignore[index]
    assert any("context" in call for call in runner.calls)


def test_security_capability_failure_is_not_reported_ready(monkeypatch) -> None:
    runner = DockerRunner(security='["name=apparmor"]')
    monkeypatch.setattr(
        "reproscout.onboarding.resolve_docker_command", lambda: ("docker",)
    )

    inspection = inspect_docker(runner=runner)

    assert inspection.daemon_running is True
    assert inspection.ready is False
    assert inspection.sandbox_security["seccomp"] == "missing"


def test_windows_11_proposes_only_the_trusted_package_manager_action(
    monkeypatch,
) -> None:
    runner = DockerRunner(running=False)
    monkeypatch.setattr("reproscout.onboarding.resolve_docker_command", _missing_docker)
    monkeypatch.setattr(
        "reproscout.onboarding.shutil.which",
        lambda name: "C:/Windows/winget.exe" if name == "winget" else None,
    )

    result = run_setup(
        host=_host("Windows", version="10.0.22631"),
        runner=runner,
        dry_run=True,
    )

    assert result.status is SetupStatus.NEEDS_SETUP
    assert result.actions[0].command == (
        "winget",
        "install",
        "--id",
        "Docker.DockerDesktop",
        "--exact",
    )
    assert result.completed_actions == ()


def test_missing_windows_package_manager_is_actionable(monkeypatch) -> None:
    monkeypatch.setattr("reproscout.onboarding.resolve_docker_command", _missing_docker)
    monkeypatch.setattr("reproscout.onboarding.shutil.which", lambda name: None)

    result = run_setup(
        host=_host("Windows", version="10.0.22631"),
        runner=DockerRunner(running=False),
        dry_run=True,
    )

    assert result.status is SetupStatus.NEEDS_SETUP
    assert result.actions == ()
    assert "winget" in result.message


def test_stopped_docker_desktop_gets_a_bounded_start_action(monkeypatch) -> None:
    runner = DockerRunner(running=False)
    monkeypatch.setattr(
        "reproscout.onboarding.resolve_docker_command", lambda: ("docker",)
    )

    result = run_setup(
        host=_host("Windows", version="10.0.22631"),
        runner=runner,
        dry_run=True,
    )

    assert result.status is SetupStatus.NEEDS_SETUP
    assert result.actions[0].command == ("docker", "desktop", "start")


def test_declining_setup_makes_no_installer_call(monkeypatch) -> None:
    runner = DockerRunner(running=False)
    monkeypatch.setattr("reproscout.onboarding.resolve_docker_command", _missing_docker)
    monkeypatch.setattr(
        "reproscout.onboarding.shutil.which",
        lambda name: "winget" if name == "winget" else None,
    )

    result = run_setup(
        host=_host("Windows", version="10.0.22631"),
        runner=runner,
        consent=False,
    )

    assert result.status is SetupStatus.DECLINED
    assert not any(call[0] == "winget" for call in runner.calls)


def test_zero_exit_installer_does_not_fake_runtime_success(monkeypatch) -> None:
    runner = DockerRunner(running=False)
    monkeypatch.setattr("reproscout.onboarding.resolve_docker_command", _missing_docker)
    monkeypatch.setattr(
        "reproscout.onboarding.shutil.which",
        lambda name: "winget" if name == "winget" else None,
    )

    def installer_only(command: Sequence[str], timeout: float) -> CommandResult:
        if command[0] == "winget":
            return CommandResult(0)
        return runner(command, timeout)

    result = run_setup(
        host=_host("Windows", version="10.0.22631"),
        runner=installer_only,
        consent=True,
    )

    assert result.status is SetupStatus.RESTART_REQUIRED
    assert result.completed_actions == (
        "Install Docker Desktop from the Windows package manager",
    )


def test_installer_permission_failure_is_reported_as_admin_required(
    monkeypatch,
) -> None:
    monkeypatch.setattr("reproscout.onboarding.resolve_docker_command", _missing_docker)
    monkeypatch.setattr(
        "reproscout.onboarding.shutil.which",
        lambda name: "winget" if name == "winget" else None,
    )

    def denied(command: Sequence[str], timeout: float) -> CommandResult:
        del command, timeout
        return CommandResult(740, stderr="The requested operation requires elevation")

    result = run_setup(
        host=_host("Windows", version="10.0.22631"),
        runner=denied,
        consent=True,
    )

    assert result.status is SetupStatus.ADMIN_REQUIRED


def test_interrupted_setup_is_resumable(monkeypatch) -> None:
    monkeypatch.setattr("reproscout.onboarding.resolve_docker_command", _missing_docker)
    monkeypatch.setattr(
        "reproscout.onboarding.shutil.which",
        lambda name: "winget" if name == "winget" else None,
    )

    def interrupted(command: Sequence[str], timeout: float) -> CommandResult:
        del command, timeout
        raise KeyboardInterrupt

    result = run_setup(
        host=_host("Windows", version="10.0.22631"),
        runner=interrupted,
        consent=True,
    )

    assert result.status is SetupStatus.INTERRUPTED
    assert "rerun setup" in result.message


def test_runtime_probe_failure_is_not_reported_ready(monkeypatch) -> None:
    runner = DockerRunner()
    monkeypatch.setattr(
        "reproscout.onboarding.resolve_docker_command", lambda: ("docker",)
    )

    result = run_setup(
        host=_host("Linux", version="6.8.0"),
        runner=runner,
        readiness_probe=lambda command: (False, "sandbox security probe failed"),
        consent=True,
    )

    assert result.status is SetupStatus.FAILED
    assert "security" in result.message


def test_running_docker_with_unverified_seccomp_does_not_report_ready(
    monkeypatch,
) -> None:
    runner = DockerRunner(security='["name=apparmor"]')
    monkeypatch.setattr(
        "reproscout.onboarding.resolve_docker_command", lambda: ("docker",)
    )
    probed = False

    def probe(command: Sequence[str] | None) -> tuple[bool, str]:
        nonlocal probed
        del command
        probed = True
        return True, "ready"

    result = run_setup(
        host=_host("Linux", version="6.8.0"),
        runner=runner,
        readiness_probe=probe,
    )

    assert result.status is SetupStatus.UNVERIFIED
    assert "seccomp" in result.message
    assert probed is False


def test_running_docker_with_unverified_context_does_not_report_ready(
    monkeypatch,
) -> None:
    runner = DockerRunner(context_verified=False)
    monkeypatch.setattr(
        "reproscout.onboarding.resolve_docker_command", lambda: ("docker",)
    )
    probed = False

    def probe(command: Sequence[str] | None) -> tuple[bool, str]:
        nonlocal probed
        del command
        probed = True
        return True, "ready"

    result = run_setup(
        host=_host("Linux", version="6.8.0"),
        runner=runner,
        readiness_probe=probe,
    )

    assert result.status is SetupStatus.UNVERIFIED
    assert "context" in result.message
    assert probed is False


def test_installer_diagnostics_are_bounded_and_redacted(monkeypatch) -> None:
    monkeypatch.setattr("reproscout.onboarding.resolve_docker_command", _missing_docker)
    monkeypatch.setattr(
        "reproscout.onboarding.shutil.which",
        lambda name: "winget" if name == "winget" else None,
    )

    def failed(command: Sequence[str], timeout: float) -> CommandResult:
        del command, timeout
        return CommandResult(1, stderr='TOKEN="secret" ' + "x" * 10_000)

    result = run_setup(
        host=_host("Windows", version="10.0.22631"),
        runner=failed,
        consent=True,
    )

    assert result.status is SetupStatus.FAILED
    assert len(result.message) <= 1_200
    assert "secret" not in result.message
    assert "secret" not in "\n".join(result.output)


def test_installer_timeout_is_not_classified_as_restart_required(monkeypatch) -> None:
    monkeypatch.setattr("reproscout.onboarding.resolve_docker_command", _missing_docker)
    monkeypatch.setattr(
        "reproscout.onboarding.shutil.which",
        lambda name: "winget" if name == "winget" else None,
    )

    def timed_out(command: Sequence[str], timeout: float) -> CommandResult:
        del command, timeout
        return CommandResult(None, timed_out=True, error="command timed out")

    result = run_setup(
        host=_host("Windows", version="10.0.22631"),
        runner=timed_out,
        consent=True,
    )

    assert result.status is SetupStatus.FAILED


def test_installation_with_unverified_followup_does_not_report_ready(
    monkeypatch,
) -> None:
    runner = DockerRunner(running=False, security='["name=apparmor"]')

    def resolve_docker() -> tuple[str, ...]:
        if runner.running:
            return ("docker",)
        return _missing_docker()

    monkeypatch.setattr("reproscout.onboarding.resolve_docker_command", resolve_docker)
    monkeypatch.setattr(
        "reproscout.onboarding.shutil.which",
        lambda name: "winget" if name == "winget" else None,
    )
    probed = False

    def probe(command: Sequence[str] | None) -> tuple[bool, str]:
        nonlocal probed
        del command
        probed = True
        return True, "ready"

    result = run_setup(
        host=_host("Windows", version="10.0.22631"),
        runner=runner,
        readiness_probe=probe,
        consent=True,
    )

    assert result.status is SetupStatus.UNVERIFIED
    assert "seccomp" in result.message
    assert probed is False


def test_successful_linux_setup_requires_the_readiness_probe(monkeypatch) -> None:
    runner = DockerRunner()
    monkeypatch.setattr(
        "reproscout.onboarding.resolve_docker_command", lambda: ("docker",)
    )
    probed: list[tuple[str, ...] | None] = []

    result = run_setup(
        host=_host("Linux", version="6.8.0"),
        runner=runner,
        readiness_probe=lambda command: (probed.append(command) or True, "ready"),
    )

    assert result.status is SetupStatus.READY
    assert probed == [("docker",)]


def test_healthy_running_docker_is_reused_without_installation(monkeypatch) -> None:
    runner = DockerRunner()
    monkeypatch.setattr(
        "reproscout.onboarding.resolve_docker_command", lambda: ("docker",)
    )

    result = run_setup(
        host=_host("Linux", version="6.8.0"),
        runner=runner,
        readiness_probe=lambda command: (True, "ready"),
    )

    assert result.status is SetupStatus.READY
    assert not any(
        call and call[0] in {"winget", "brew", "colima"} for call in runner.calls
    )


def test_repeated_setup_reuses_a_now_running_runtime(monkeypatch) -> None:
    runner = DockerRunner(running=False)

    def resolve_docker() -> tuple[str, ...]:
        if runner.running:
            return ("docker",)
        return _missing_docker()

    monkeypatch.setattr("reproscout.onboarding.resolve_docker_command", resolve_docker)
    monkeypatch.setattr(
        "reproscout.onboarding.shutil.which",
        lambda name: "winget" if name == "winget" else None,
    )
    readiness = lambda command: (True, "ready")

    first = run_setup(
        host=_host("Windows", version="10.0.22631"),
        runner=runner,
        readiness_probe=readiness,
        consent=True,
    )
    second = run_setup(
        host=_host("Windows", version="10.0.22631"),
        runner=runner,
        readiness_probe=readiness,
        consent=False,
    )

    assert first.status is SetupStatus.READY
    assert second.status is SetupStatus.READY
    assert second.actions == ()


def test_macos_proposes_homebrew_colima_setup_when_docker_is_missing(
    monkeypatch,
) -> None:
    monkeypatch.setattr("reproscout.onboarding.resolve_docker_command", _missing_docker)
    monkeypatch.setattr(
        "reproscout.onboarding.shutil.which",
        lambda name: "brew" if name == "brew" else None,
    )

    result = run_setup(
        host=_host("Darwin", version="14.5.0"),
        runner=DockerRunner(running=False),
        dry_run=True,
    )

    assert [action.command for action in result.actions] == [
        ("brew", "install", "docker", "colima"),
        ("colima", "start"),
    ]


def test_macos_colima_endpoint_is_selected_without_changing_context(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "reproscout.onboarding.resolve_docker_command", lambda: ("docker",)
    )
    monkeypatch.setattr("reproscout.onboarding.shutil.which", lambda name: "colima")

    def runner(command: Sequence[str], timeout: float) -> CommandResult:
        del timeout
        values = tuple(command)
        if "--context" in values:
            if values[-1] == "{{json .SecurityOptions}}":
                return CommandResult(0, '["name=seccomp"]')
            if values[3:5] == ("context", "show"):
                return CommandResult(0, "colima\n")
            return CommandResult(0, "27.0.1\n")
        return CommandResult(1, stderr="default context is stopped")

    inspection = inspect_docker(_host("Darwin", version="14.5.0"), runner=runner)

    assert inspection.command == ("docker", "--context", "colima")
    assert inspection.context == "colima"
    assert inspection.ready is True


@pytest.mark.parametrize(
    ("system", "version"),
    [("Darwin", "14.5.0"), ("Plan9", "1")],
)
def test_non_linux_platforms_are_explicitly_unverified_or_unsupported(
    system: str,
    version: str,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "reproscout.onboarding.resolve_docker_command", lambda: ("docker",)
    )
    result = run_setup(
        host=_host(system, version=version),
        runner=DockerRunner(),
        readiness_probe=lambda command: (False, "workspace ownership is unverified"),
    )

    assert result.status in {SetupStatus.UNVERIFIED, SetupStatus.UNSUPPORTED}
