from __future__ import annotations

from pathlib import Path

import pytest

from reproscout.application import AuditRequest, AuditService, ServiceError
from reproscout.execution import PlanExecutionEngine
from reproscout.repo.clone import CloneResult
from reproscout.sandbox import ExecutionResult, Sandbox, SandboxConfig
from reproscout.status import ReproductionStatus


class FixtureSandbox(Sandbox):
    def __init__(self, config: SandboxConfig, responses: list[ExecutionResult]) -> None:
        self.config = config
        self.responses = responses
        self.created = False
        self.destroyed = False

    def create(self) -> None:
        self.created = True

    def execute(
        self, command: str, *, timeout_seconds: float | None = None
    ) -> ExecutionResult:
        del timeout_seconds
        response = self.responses.pop(0)
        return response.model_copy(update={"command": command})

    def destroy(self) -> None:
        self.destroyed = True


def _execution(*, exit_code: int = 0, stderr: str = "") -> ExecutionResult:
    return ExecutionResult(
        command="fixture",
        stdout="fixture output\n" if exit_code == 0 else "",
        stderr=stderr,
        exit_code=exit_code,
        duration=0.01,
        timed_out=False,
        container_id="fixture-container",
    )


def _service(
    tmp_path: Path, files: dict[str, str], responses: list[list[ExecutionResult]]
) -> AuditService:
    def clone(url: str, destination: Path) -> CloneResult:
        del url
        destination.mkdir(parents=True)
        for relative, contents in files.items():
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents, encoding="utf-8")
        return CloneResult(
            repository_url="https://github.com/example/fixture",
            workspace_path=destination,
            commit_sha="a" * 40,
            branch="main",
        )

    def cleanroom_clone(url: str, destination: Path, commit_sha: str) -> CloneResult:
        del url
        destination.mkdir(parents=True)
        for relative, contents in files.items():
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents, encoding="utf-8")
        return CloneResult(
            repository_url="https://github.com/example/fixture",
            workspace_path=destination,
            commit_sha=commit_sha,
            branch=None,
        )

    def execution_factory(store):
        response_set = responses.pop(0)

        def sandbox_factory(config: SandboxConfig) -> FixtureSandbox:
            return FixtureSandbox(config, response_set)

        return PlanExecutionEngine(store, sandbox_factory=sandbox_factory)

    return AuditService(
        tmp_path / "runs",
        clone_fn=clone,
        cleanroom_clone_fn=cleanroom_clone,
        execution_factory=execution_factory,
    )


def _request() -> AuditRequest:
    return AuditRequest(
        repository_url="https://github.com/example/fixture",
        no_ai=True,
    )


def test_audit_success_requires_clean_room_and_writes_report(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        {
            "README.md": "Run with `python app.py`.\n",
            "pyproject.toml": "[project]\nname='fixture'\nversion='0.1.0'\n",
            "app.py": "print('ok')\n",
        },
        [
            [_execution(), _execution()],
            [_execution(), _execution()],
            [_execution(), _execution()],
        ],
    )

    result = service.audit(_request())

    assert result.status.status is ReproductionStatus.REPRODUCED
    assert result.status.clean_room_verified is True
    assert result.report_path.is_file()
    assert result.reproduction_package_path is not None
    assert (result.report_path.parent / "plan.json").is_file()
    assert (result.report_path.parent / "run.json").is_file()

    replay = service.reproduce(result.run_id)

    assert replay.status.status is ReproductionStatus.REPRODUCED
    assert replay.source_run_id == result.run_id


def test_audit_failure_is_objectively_reported(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        {"README.md": "python app.py\n", "app.py": "print('ok')\n"},
        [[_execution(exit_code=7, stderr="fixture failed")]],
    )

    result = service.audit(_request())

    assert result.status.status is ReproductionStatus.FAILED
    assert result.status.workflow_succeeded is False
    report = result.report_path.read_text(encoding="utf-8")
    assert "fixture failed" in report


def test_audit_without_executable_evidence_is_blocked(tmp_path: Path) -> None:
    service = _service(
        tmp_path, {"README.md": "No runnable workflow is documented.\n"}, [[]]
    )

    result = service.audit(_request())

    assert result.status.status is ReproductionStatus.BLOCKED
    assert result.status.verification_status is None
    assert result.repairs == 0


def test_audit_rejects_unsafe_repository_url(tmp_path: Path) -> None:
    service = AuditService(tmp_path / "runs")

    with pytest.raises(ServiceError, match="Only HTTPS GitHub"):
        service.audit(
            AuditRequest.model_construct(
                repository_url="https://gitlab.com/example/fixture",
                goal="auto",
                no_ai=True,
            )
        )
