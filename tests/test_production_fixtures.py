from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from reproscout.application import AuditRequest, AuditService, ServiceError
from reproscout.evaluation import load_evaluation_set
from reproscout.execution import PlanExecutionEngine
from reproscout.repo.clone import CloneResult
from reproscout.sandbox import ExecutionResult, Sandbox, SandboxConfig
from reproscout.status import ReproductionStatus
from reproscout.verification import VerificationLevel


class _FixtureSandbox(Sandbox):
    def __init__(self, config: SandboxConfig, response: ExecutionResult) -> None:
        self.config = config
        self.response = response

    def create(self) -> None:
        return None

    def execute(
        self,
        command: str,
        *,
        timeout_seconds: float | None = None,
    ) -> ExecutionResult:
        del timeout_seconds
        return self.response.model_copy(update={"command": command})

    def destroy(self) -> None:
        return None


_EXPECTED: dict[str, tuple[ReproductionStatus, VerificationLevel, bool | None]] = {
    "eval-001": (ReproductionStatus.REPRODUCED, VerificationLevel.L2, True),
    "eval-002": (ReproductionStatus.REPRODUCED, VerificationLevel.L3, True),
    "eval-003": (ReproductionStatus.REPRODUCED, VerificationLevel.L2, True),
    "eval-004": (ReproductionStatus.REPRODUCED, VerificationLevel.L2, True),
    "eval-005": (ReproductionStatus.FAILED, VerificationLevel.L0, None),
    "eval-006": (ReproductionStatus.FAILED, VerificationLevel.L0, None),
    "eval-007": (ReproductionStatus.BLOCKED, VerificationLevel.L0, None),
    "eval-008": (ReproductionStatus.BLOCKED, VerificationLevel.L0, None),
    "eval-009": (ReproductionStatus.FAILED, VerificationLevel.L0, None),
    "eval-010": (ReproductionStatus.FAILED, VerificationLevel.L0, None),
    "eval-011": (ReproductionStatus.FAILED, VerificationLevel.L0, None),
    "eval-012": (ReproductionStatus.FAILED, VerificationLevel.L0, None),
    "eval-013": (ReproductionStatus.PARTIAL, VerificationLevel.L2, False),
    "eval-014": (ReproductionStatus.PARTIAL, VerificationLevel.L2, False),
    "eval-015": (ReproductionStatus.FAILED, VerificationLevel.L0, None),
}
_EXPECTED_FAILURES = {
    "eval-005": "MISSING_PACKAGE",
    "eval-006": "DEPENDENCY_CONFLICT",
    "eval-007": "UNKNOWN",
    "eval-008": "UNKNOWN",
    "eval-009": "NETWORK_FAILURE",
    "eval-010": "AUTH_REQUIRED",
    "eval-011": "UNKNOWN",
    "eval-012": "TEST_FAILURE",
    "eval-015": "TIMEOUT",
}


def _response(case_id: str, *, clean_room: bool) -> ExecutionResult:
    failure_messages = {
        "eval-005": "ModuleNotFoundError: No module named 'missing_package'",
        "eval-006": "ResolutionImpossible: dependency conflict",
        "eval-009": "Network is unreachable",
        "eval-010": "401 Unauthorized: credentials required",
        "eval-011": "invalid configuration path",
        "eval-012": "1 failed, 0 passed",
    }
    if case_id in failure_messages:
        return ExecutionResult(
            command="fixture",
            stdout="",
            stderr=failure_messages[case_id],
            exit_code=1,
            duration=0.01,
            timed_out=False,
            container_id="fixture-container",
        )
    if case_id == "eval-015":
        return ExecutionResult(
            command="fixture",
            stdout="",
            stderr="command timed out",
            exit_code=124,
            duration=0.01,
            timed_out=True,
            container_id="fixture-container",
        )
    if clean_room and case_id in {"eval-013", "eval-014"}:
        return ExecutionResult(
            command="fixture",
            stdout="",
            stderr="clean-room input was not reproduced",
            exit_code=1,
            duration=0.01,
            timed_out=False,
            container_id="fixture-container",
        )
    stdout = "1 passed\n" if case_id == "eval-002" else "fixture passed\n"
    return ExecutionResult(
        command="fixture",
        stdout=stdout,
        stderr="",
        exit_code=0,
        duration=0.01,
        timed_out=False,
        container_id="fixture-container",
    )


def _run_fixture(case_id: str, root: Path):
    case = next(case for case in load_evaluation_set().cases if case.case_id == case_id)
    source = Path(case.fixture_path)
    run_root = root / case_id
    sandbox_calls = 0

    def clone(url: str, destination: Path) -> CloneResult:
        del url
        shutil.copytree(source, destination)
        return CloneResult(
            repository_url="https://github.com/example/fixture",
            workspace_path=destination,
            commit_sha="c" * 40,
            branch=None,
        )

    def cleanroom_clone(url: str, destination: Path, commit_sha: str) -> CloneResult:
        del url
        shutil.copytree(source, destination)
        return CloneResult(
            repository_url="https://github.com/example/fixture",
            workspace_path=destination,
            commit_sha=commit_sha,
            branch=None,
        )

    def execution_factory(store):
        def sandbox_factory(config: SandboxConfig) -> _FixtureSandbox:
            nonlocal sandbox_calls
            clean_room = sandbox_calls > 0
            sandbox_calls += 1
            return _FixtureSandbox(
                config,
                _response(case_id, clean_room=clean_room),
            )

        return PlanExecutionEngine(
            store,
            sandbox_factory=sandbox_factory,
        )

    service = AuditService(
        run_root / "runs",
        clone_fn=clone,
        cleanroom_clone_fn=cleanroom_clone,
        execution_factory=execution_factory,
    )
    if case_id == "eval-016":
        with pytest.raises(ServiceError):
            service.audit(
                AuditRequest(
                    repository_url="https://github.com/example/fixture",
                    goal=case.goal,
                    no_ai=True,
                )
            )
        return None
    result = service.audit(
        AuditRequest(
            repository_url="https://github.com/example/fixture",
            goal=case.goal,
            no_ai=True,
        )
    )
    report_json = json.loads(result.report_path.with_name("run.json").read_text())
    failure_class = (
        report_json["failures"][0]["failure_class"] if report_json["failures"] else None
    )
    return result, failure_class


@pytest.mark.integration
def test_production_fixture_suite_uses_audit_service_and_checks_false_reproduced(
    tmp_path: Path,
) -> None:
    evaluation_set = load_evaluation_set()
    assert len(evaluation_set.cases) == 20
    assert all(Path(case.fixture_path).is_dir() for case in evaluation_set.cases)
    assert len(_EXPECTED) == 15
    false_reproduced = 0
    observed: dict[str, str] = {}
    for case_id, (
        expected_status,
        expected_level,
        expected_clean_room,
    ) in _EXPECTED.items():
        result, failure_class = _run_fixture(case_id, tmp_path)
        assert result is not None
        observed[case_id] = result.status.status.value
        assert result.status.status is expected_status
        assert result.status.verification_level is expected_level
        assert result.status.clean_room_verified is expected_clean_room
        if case_id in _EXPECTED_FAILURES:
            assert failure_class == _EXPECTED_FAILURES[case_id]
        if (
            result.status.status is ReproductionStatus.REPRODUCED
            and expected_status is not ReproductionStatus.REPRODUCED
        ):
            false_reproduced += 1
    assert false_reproduced == 0
    assert len(observed) == 15


@pytest.mark.integration
def test_unsafe_fixture_is_rejected_before_execution(tmp_path: Path) -> None:
    assert _run_fixture("eval-016", tmp_path) is None
