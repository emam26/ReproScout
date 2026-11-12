from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from pathlib import Path

import pytest

from reproscout.application import AuditService, PreflightRequest, ServiceError
from reproscout.execution import PlanExecutionEngine
from reproscout.preflight import PreflightStatus
from reproscout.repo.clone import CloneError, CloneResult
from reproscout.sandbox import (
    DockerSandbox,
    ExecutionResult,
    SandboxConfig,
    SandboxSecurityError,
)


_FIXTURES = Path(__file__).parent / "fixtures" / "preflight"


def _fingerprint() -> str:
    return json.dumps(
        {
            "architecture": "x86_64",
            "compiler": "GCC fixture",
            "cuda_runtime": None,
            "environment_variable_names": ["PATH"],
            "gpu_visible": False,
            "installed_packages": [{"name": "pip", "version": "25.0"}],
            "os_name": "Linux",
            "os_release": "fixture",
            "pip_version": "25.0",
            "python_version": "3.11.9",
        }
    )


class _PreflightSandbox(DockerSandbox):
    def __init__(
        self,
        config: SandboxConfig,
        *,
        commands: list[str],
        fail_install: bool = False,
        fail_check: bool = False,
        fail_create: bool = False,
    ) -> None:
        super().__init__(config, docker_command=("docker",))
        self.commands = commands
        self.fail_install = fail_install
        self.fail_check = fail_check
        self.fail_create = fail_create

    def create(self) -> None:
        if self.fail_create:
            raise SandboxSecurityError(
                'Docker sandbox initialization failed\nTOKEN="secret"\t'
                'path="/workspace"\x1b[31m'
            )
        self.container_id = "fixture-container"

    def execute(
        self,
        command: str | Sequence[str],
        *,
        timeout_seconds: float | None = None,
    ) -> ExecutionResult:
        del timeout_seconds
        text = command if isinstance(command, str) else " ".join(command)
        self.commands.append(text)
        if self.fail_install and "pip install" in text:
            return ExecutionResult(
                command=text,
                stdout="",
                stderr="controlled dependency installation failure",
                exit_code=1,
                duration=0.01,
                timed_out=False,
                container_id=self.container_id,
            )
        if self.fail_check and text == "python -m pip check":
            return ExecutionResult(
                command=text,
                stdout="",
                stderr="controlled pip verification failure",
                exit_code=1,
                duration=0.01,
                timed_out=False,
                container_id=self.container_id,
            )
        if (
            not isinstance(command, str)
            and len(command) >= 3
            and command[:2] == ["python", "-c"]
        ):
            return self._result(_fingerprint())
        if text == "python --version":
            return self._result("Python 3.11.9\n")
        if text == "python -m pip --version":
            return self._result("pip 25.0 from /workspace/.reproscout/venv\n")
        if text == "python -m pip check":
            return self._result("No broken requirements found.\n")
        return self._result("")

    def _result(self, stdout: str) -> ExecutionResult:
        return ExecutionResult(
            command="fixture",
            stdout=stdout,
            stderr="",
            exit_code=0,
            duration=0.01,
            timed_out=False,
            container_id=self.container_id,
        )

    def destroy(self) -> None:
        self.container_id = None


def _service(
    tmp_path: Path,
    fixture_name: str,
    *,
    fail_install: bool = False,
    fail_check: bool = False,
    fail_create: bool = False,
    commands: list[str] | None = None,
) -> AuditService:
    source = _FIXTURES / fixture_name
    recorded = commands if commands is not None else []

    def clone(url: str, destination: Path) -> CloneResult:
        shutil.copytree(source, destination)
        return CloneResult(
            repository_url=url,
            workspace_path=destination,
            commit_sha="a" * 40,
            branch="main",
        )

    def execution_factory(store):
        def factory(config: SandboxConfig) -> _PreflightSandbox:
            return _PreflightSandbox(
                config,
                commands=recorded,
                fail_install=fail_install,
                fail_check=fail_check,
                fail_create=fail_create,
            )

        return PlanExecutionEngine(store, sandbox_factory=factory)

    return AuditService(
        tmp_path / "runs",
        clone_fn=clone,
        execution_factory=execution_factory,
    )


def _request() -> PreflightRequest:
    return PreflightRequest(
        repository_url="https://github.com/example/preflight-fixture",
        no_ai=True,
    )


def test_preflight_requirements_fixture_reaches_reported_ready_state(
    tmp_path: Path,
) -> None:
    commands: list[str] = []
    result = _service(tmp_path, "fixture_b_requirements", commands=commands).preflight(
        _request()
    )

    assert result.status is PreflightStatus.READY
    assert result.verified_environment is True
    evidence = json.loads(result.artifact_paths.environment_evidence.read_text())
    assert evidence["run_id"] == result.run_id
    assert evidence["commit_sha"] == "a" * 40
    assert all("train.py" not in command for command in commands)
    assert result.artifact_paths.environment_report.is_file()
    assert result.artifact_paths.agent_handoff.is_file()


def test_preflight_minimal_fixture_reaches_ready_without_external_packages(
    tmp_path: Path,
) -> None:
    result = _service(tmp_path, "fixture_a_minimal").preflight(_request())

    assert result.status is PreflightStatus.READY
    assert result.verified_environment is True
    handoff = result.artifact_paths.agent_handoff.read_text(encoding="utf-8")
    assert "training" in handoff


def test_preflight_pyproject_fixture_preserves_runtime_and_optional_evidence(
    tmp_path: Path,
) -> None:
    result = _service(tmp_path, "fixture_c_pyproject").preflight(_request())

    assert result.status is PreflightStatus.PARTIAL
    evidence = json.loads(result.artifact_paths.environment_evidence.read_text())
    inspection = evidence["inspection"]
    assert inspection["python_constraints"] == [">=3.10,<3.12"]
    assert inspection["optional_package_requirements"][0]["name"] == "torch"
    assert "optional" in result.artifact_paths.environment_report.read_text(
        encoding="utf-8"
    ).lower()


def test_preflight_malformed_dependency_is_partial_not_ready(tmp_path: Path) -> None:
    result = _service(tmp_path, "fixture_d_invalid").preflight(_request())

    assert result.status is PreflightStatus.PARTIAL
    evidence = json.loads(result.artifact_paths.environment_evidence.read_text())
    assert evidence["inspection"]["dependency_parse_errors"]
    assert result.verified_environment is False


def test_preflight_unsafe_readme_stops_before_sandbox_execution(
    tmp_path: Path,
) -> None:
    commands: list[str] = []
    result = _service(tmp_path, "fixture_e_unsafe", commands=commands).preflight(
        _request()
    )

    assert result.status is PreflightStatus.UNSAFE
    assert commands == []
    evidence = json.loads(
        result.artifact_paths.environment_evidence.read_text(encoding="utf-8")
    )
    assert any(
        "sudo apt-get install ffmpeg" in finding
        for finding in evidence["inspection"]["unsafe_findings"]
    )
    report = result.artifact_paths.environment_report.read_text(encoding="utf-8")
    handoff = result.artifact_paths.agent_handoff.read_text(encoding="utf-8")
    assert "sudo apt-get install ffmpeg" in report
    assert "sudo apt-get install ffmpeg" in handoff
    assert "Untrusted repository command" in handoff
    assert "not executed" in handoff


def test_preflight_setup_failure_preserves_command_outcome(tmp_path: Path) -> None:
    result = _service(
        tmp_path,
        "fixture_f_verification_failure",
        fail_install=True,
    ).preflight(_request())

    assert result.status is PreflightStatus.INSTALLATION_FAILED
    evidence = json.loads(result.artifact_paths.environment_evidence.read_text())
    failed = [
        item
        for item in evidence["assessment"]["commands"]
        if item["outcome"] == "FAILED"
    ]
    assert failed
    assert failed[0]["exit_code"] == 1


def test_preflight_verification_failure_preserves_command_outcome(
    tmp_path: Path,
) -> None:
    result = _service(
        tmp_path,
        "fixture_f_verification_failure",
        fail_check=True,
    ).preflight(_request())

    assert result.status is PreflightStatus.INSTALLATION_FAILED
    evidence = json.loads(result.artifact_paths.environment_evidence.read_text())
    failed = [
        item
        for item in evidence["assessment"]["commands"]
        if item["command"] == "python -m pip check"
    ]
    assert failed[0]["outcome"] == "FAILED"
    assert failed[0]["exit_code"] == 1
    assert result.verified_environment is False


def test_preflight_sandbox_failure_is_unverified_and_redacted(tmp_path: Path) -> None:
    result = _service(
        tmp_path,
        "fixture_b_requirements",
        fail_create=True,
    ).preflight(_request())

    assert result.status is PreflightStatus.UNVERIFIED
    evidence_text = result.artifact_paths.environment_evidence.read_text(
        encoding="utf-8"
    )
    evidence = json.loads(evidence_text)
    assert any(
        command["failure_kind"] == "SANDBOX_FAILURE"
        for command in evidence["assessment"]["commands"]
    )
    assert "Docker sandbox initialization failed" in evidence_text
    assert "secret" not in evidence_text
    assert "secret" not in result.artifact_paths.environment_report.read_text(
        encoding="utf-8"
    )
    assert "secret" not in result.artifact_paths.agent_handoff.read_text(
        encoding="utf-8"
    )


def test_preflight_clone_failure_writes_unverified_artifacts(tmp_path: Path) -> None:
    def clone(url: str, destination: Path) -> CloneResult:
        del url, destination
        raise CloneError('Git failed\nTOKEN="secret"\tpath="/tmp/repo"')

    service = AuditService(tmp_path / "runs", clone_fn=clone)

    with pytest.raises(ServiceError, match="Artifacts") as error:
        service.preflight(_request())

    assert "secret" not in str(error.value)
    artifact_dirs = [path for path in (tmp_path / "runs").iterdir() if path.is_dir()]
    assert len(artifact_dirs) == 1
    evidence = json.loads(
        (artifact_dirs[0] / "environment-evidence.json").read_text()
    )
    assert evidence["commit_sha"] is None
    assert evidence["verified_environment"] is False
    assert "Git failed" in evidence["failure"]["detail"]
    assert (artifact_dirs[0] / "environment-report.md").is_file()
    assert (artifact_dirs[0] / "agent-handoff.md").is_file()
