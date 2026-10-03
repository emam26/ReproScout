from __future__ import annotations

import json
from pathlib import Path

import pytest

from reproscout.execution import (
    ExecutionEngineError,
    ExecutionFailureKind,
    ExecutionLimits,
    PlanExecutionEngine,
)
from reproscout.planning import (
    PlanActionType,
    PlanBaseline,
    PlanStep,
    ReproductionPlan,
    RiskLevel,
)
from reproscout.sandbox import ExecutionResult, Sandbox, SandboxConfig, SandboxError
from reproscout.state import EventType, RunOutcome, SQLiteRunStore, Stage


class FakeSandbox(Sandbox):
    def __init__(
        self,
        config: SandboxConfig,
        responses: list[ExecutionResult | SandboxError],
        *,
        create_error: SandboxError | None = None,
    ) -> None:
        self.config = config
        self.responses = responses
        self.create_error = create_error
        self.created = False
        self.destroyed = False
        self.commands: list[tuple[str, float | None]] = []

    def create(self) -> None:
        if self.create_error is not None:
            raise self.create_error
        self.created = True

    def execute(
        self,
        command: str,
        *,
        timeout_seconds: float | None = None,
    ) -> ExecutionResult:
        self.commands.append((command, timeout_seconds))
        response = self.responses.pop(0)
        if isinstance(response, SandboxError):
            raise response
        return response

    def destroy(self) -> None:
        self.destroyed = True


def _result(
    command: str,
    *,
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
    timed_out: bool = False,
) -> ExecutionResult:
    return ExecutionResult(
        command=command,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        duration=0.01,
        timed_out=timed_out,
        container_id="container-id",
    )


def _step(
    step_id: str,
    command: str | None,
    *,
    action: PlanActionType = PlanActionType.RUN_DEMO,
    working_directory: str = ".",
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        action_type=action,
        command=command,
        working_directory=working_directory,
        purpose="Controlled fixture step.",
        timeout_seconds=10,
        network_required=False,
        expected_outcome="The command exits with code zero.",
        risk=RiskLevel.LOW,
    )


def _plan(steps: list[PlanStep]) -> ReproductionPlan:
    return ReproductionPlan(
        repository="example/project",
        commit_sha="a" * 40,
        goal="auto",
        baseline=PlanBaseline.OFFICIAL_DOCUMENTATION,
        steps=steps,
        overall_timeout_seconds=60,
    )


def _create_planned_run(store: SQLiteRunStore) -> str:
    run = store.create_run(context={"repository": "example/project"})
    store.transition(run.run_id, Stage.ANALYZE)
    store.transition(run.run_id, Stage.PLAN)
    return run.run_id


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    run_directory = tmp_path / "run"
    workspace = run_directory / "workspace"
    workspace.mkdir(parents=True)
    return run_directory, workspace


def test_successful_plan_runs_in_order_and_persists_evidence(tmp_path: Path) -> None:
    run_directory, workspace = _workspace(tmp_path)
    responses = [
        _result("install", stdout="installed\n"),
        _result("demo", stdout="demo output\n", stderr="warning\n"),
    ]
    created: list[FakeSandbox] = []

    def factory(config: SandboxConfig) -> FakeSandbox:
        sandbox = FakeSandbox(config, responses)
        created.append(sandbox)
        return sandbox

    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        run_id = _create_planned_run(store)
        engine = PlanExecutionEngine(store, sandbox_factory=factory)
        result = engine.execute(
            _plan(
                [
                    _step(
                        "step-001",
                        "python -m pip install .",
                        action=PlanActionType.INSTALL_DEPENDENCY,
                    ),
                    _step("step-002", "python demo.py"),
                ]
            ),
            run_id=run_id,
            run_directory=run_directory,
            workspace=workspace,
        )
        state = store.get_run(run_id)
        events = store.list_events(run_id)

    assert result.workflow_succeeded is True
    assert state.stage is Stage.DONE
    assert state.outcome is RunOutcome.SUCCEEDED
    assert [command for command, _ in created[0].commands] == [
        "python -m pip install .",
        "python demo.py",
    ]
    assert created[0].created is True
    assert created[0].destroyed is True
    assert created[0].config.network == "none"
    assert [event.event_type for event in events].count(EventType.ATTEMPT_RECORDED) == 2
    assert [event.event_type for event in events].count(EventType.TOOL_CALLED) == 2
    assert [event.event_type for event in events].count(
        EventType.TOOL_RESULT_RECORDED
    ) == 2
    tool_calls = [
        event for event in events if event.event_type is EventType.TOOL_CALLED
    ]
    assert all(
        event.payload["tool_call"]["arguments"]["network_mode"] == "none"
        for event in tool_calls
    )
    tool_results = [
        event for event in events if event.event_type is EventType.TOOL_RESULT_RECORDED
    ]
    assert all(
        event.payload["tool_result"]["payload"]["network_mode"] == "none"
        for event in tool_results
    )
    command_lines = (
        (run_directory / "commands.jsonl").read_text(encoding="utf-8").splitlines()
    )
    assert [json.loads(line)["step_id"] for line in command_lines] == [
        "step-001",
        "step-002",
    ]
    assert "installed" in (run_directory / "logs" / "setup.log").read_text(
        encoding="utf-8"
    )
    assert "warning" in (run_directory / "logs" / "execution.log").read_text(
        encoding="utf-8"
    )
    assert (run_directory / "events.jsonl").is_file()


def test_nonzero_command_stops_plan_without_repair(tmp_path: Path) -> None:
    run_directory, workspace = _workspace(tmp_path)
    responses = [
        _result("fail", stderr="fixture failed\n", exit_code=7),
        _result("must-not-run"),
    ]
    created: list[FakeSandbox] = []

    def factory(config: SandboxConfig) -> FakeSandbox:
        sandbox = FakeSandbox(config, responses)
        created.append(sandbox)
        return sandbox

    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        run_id = _create_planned_run(store)
        result = PlanExecutionEngine(store, sandbox_factory=factory).execute(
            _plan(
                [
                    _step("step-001", "python fail.py"),
                    _step("step-002", "python later.py"),
                ]
            ),
            run_id=run_id,
            run_directory=run_directory,
            workspace=workspace,
        )
        state = store.get_run(run_id)
        events = store.list_events(run_id)

    assert result.workflow_succeeded is False
    assert result.failure_kind is ExecutionFailureKind.COMMAND_NONZERO
    assert result.failed_step_id == "step-001"
    assert len(created[0].commands) == 1
    assert state.outcome is RunOutcome.FAILED
    assert all(event.stage is not Stage.DEBUG for event in events)


def test_timeout_is_captured_and_classified(tmp_path: Path) -> None:
    run_directory, workspace = _workspace(tmp_path)

    def factory(config: SandboxConfig) -> FakeSandbox:
        return FakeSandbox(
            config,
            [_result("slow", exit_code=124, timed_out=True)],
        )

    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        run_id = _create_planned_run(store)
        result = PlanExecutionEngine(store, sandbox_factory=factory).execute(
            _plan([_step("step-001", "python slow.py")]),
            run_id=run_id,
            run_directory=run_directory,
            workspace=workspace,
        )

    assert result.steps[0].timed_out is True
    assert result.failure_kind is ExecutionFailureKind.TIMEOUT


@pytest.mark.parametrize(
    ("step", "execution", "expected"),
    [
        (
            _step(
                "step-001",
                "python -m pip install .",
                action=PlanActionType.INSTALL_DEPENDENCY,
            ),
            _result("install", stderr="build failed", exit_code=1),
            ExecutionFailureKind.DEPENDENCY_INSTALLATION_FAILURE,
        ),
        (
            _step(
                "step-001",
                "python -m pip install .",
                action=PlanActionType.INSTALL_DEPENDENCY,
            ),
            _result("install", stderr="network is unreachable", exit_code=1),
            ExecutionFailureKind.NETWORK_FAILURE,
        ),
        (
            _step("step-001", "python memory.py"),
            _result("memory", stderr="out of memory", exit_code=137),
            ExecutionFailureKind.RESOURCE_LIMIT_FAILURE,
        ),
    ],
)
def test_deterministic_failure_classification(
    tmp_path: Path,
    step: PlanStep,
    execution: ExecutionResult,
    expected: ExecutionFailureKind,
) -> None:
    run_directory, workspace = _workspace(tmp_path)

    def factory(config: SandboxConfig) -> FakeSandbox:
        return FakeSandbox(config, [execution])

    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        run_id = _create_planned_run(store)
        result = PlanExecutionEngine(store, sandbox_factory=factory).execute(
            _plan([step]),
            run_id=run_id,
            run_directory=run_directory,
            workspace=workspace,
        )

    assert result.failure_kind is expected


def test_output_is_redacted_and_bounded_in_state_and_artifacts(tmp_path: Path) -> None:
    run_directory, workspace = _workspace(tmp_path)
    secret = "super-secret-token"
    output = (
        f"GITHUB_TOKEN={secret} Bearer abcdefghijklmnop\n"
        f"Authorization: Basic {secret}\n"
        f"https://user:{secret}@example.test/archive\n" + "x" * 500
    )

    def factory(config: SandboxConfig) -> FakeSandbox:
        return FakeSandbox(config, [_result("output", stdout=output)])

    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        run_id = _create_planned_run(store)
        result = PlanExecutionEngine(
            store,
            sandbox_factory=factory,
            limits=ExecutionLimits(
                max_persisted_output_chars=100,
                max_log_output_chars=150,
            ),
        ).execute(
            _plan([_step("step-001", "python output.py")]),
            run_id=run_id,
            run_directory=run_directory,
            workspace=workspace,
        )
        serialized_events = "\n".join(
            json.dumps(event.payload) for event in store.list_events(run_id)
        )

    artifact_text = (run_directory / "commands.jsonl").read_text(encoding="utf-8")
    log_text = (run_directory / "logs" / "execution.log").read_text(encoding="utf-8")
    assert secret not in result.steps[0].stdout
    assert secret not in serialized_events
    assert secret not in artifact_text
    assert secret not in log_text
    assert "<redacted>" in log_text
    assert len(result.steps[0].stdout) <= 100
    assert result.steps[0].attempt_number == 1


def test_workspace_relative_directory_is_applied_inside_container(
    tmp_path: Path,
) -> None:
    run_directory, workspace = _workspace(tmp_path)
    (workspace / "examples").mkdir()
    created: list[FakeSandbox] = []

    def factory(config: SandboxConfig) -> FakeSandbox:
        sandbox = FakeSandbox(config, [_result("demo")])
        created.append(sandbox)
        return sandbox

    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        run_id = _create_planned_run(store)
        PlanExecutionEngine(store, sandbox_factory=factory).execute(
            _plan(
                [
                    _step(
                        "step-001",
                        "python demo.py",
                        working_directory="examples",
                    )
                ]
            ),
            run_id=run_id,
            run_directory=run_directory,
            workspace=workspace,
        )

    assert created[0].commands[0][0] == "cd -- /workspace/examples && python demo.py"


def test_commandless_prerequisite_stops_without_creating_sandbox(
    tmp_path: Path,
) -> None:
    run_directory, workspace = _workspace(tmp_path)
    factory_called = False

    def factory(config: SandboxConfig) -> FakeSandbox:
        nonlocal factory_called
        factory_called = True
        return FakeSandbox(config, [])

    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        run_id = _create_planned_run(store)
        result = PlanExecutionEngine(store, sandbox_factory=factory).execute(
            _plan(
                [
                    _step(
                        "step-001",
                        None,
                        action=PlanActionType.PREPARE_ASSET,
                    )
                ]
            ),
            run_id=run_id,
            run_directory=run_directory,
            workspace=workspace,
        )

    assert factory_called is False
    assert result.failure_kind is ExecutionFailureKind.PREREQUISITE_UNAVAILABLE


def test_sandbox_creation_failure_is_workflow_failure(tmp_path: Path) -> None:
    run_directory, workspace = _workspace(tmp_path)

    def factory(config: SandboxConfig) -> FakeSandbox:
        return FakeSandbox(config, [], create_error=SandboxError("unavailable"))

    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        run_id = _create_planned_run(store)
        result = PlanExecutionEngine(store, sandbox_factory=factory).execute(
            _plan([_step("step-001", "python demo.py")]),
            run_id=run_id,
            run_directory=run_directory,
            workspace=workspace,
        )

    assert result.failure_kind is ExecutionFailureKind.SANDBOX_FAILURE
    assert result.workflow_succeeded is False


def test_sandbox_factory_failure_is_workflow_failure(tmp_path: Path) -> None:
    run_directory, workspace = _workspace(tmp_path)

    def factory(config: SandboxConfig) -> FakeSandbox:
        raise SandboxError("invalid sandbox configuration")

    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        run_id = _create_planned_run(store)
        result = PlanExecutionEngine(store, sandbox_factory=factory).execute(
            _plan([_step("step-001", "python demo.py")]),
            run_id=run_id,
            run_directory=run_directory,
            workspace=workspace,
        )

    assert result.failure_kind is ExecutionFailureKind.SANDBOX_FAILURE
    assert result.workflow_succeeded is False


def test_execution_requires_plan_stage(tmp_path: Path) -> None:
    run_directory, workspace = _workspace(tmp_path)
    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        run = store.create_run()
        engine = PlanExecutionEngine(
            store, sandbox_factory=lambda config: FakeSandbox(config, [])
        )

        with pytest.raises(ExecutionEngineError, match="requires PLAN"):
            engine.execute(
                _plan([_step("step-001", "python demo.py")]),
                run_id=run.run_id,
                run_directory=run_directory,
                workspace=workspace,
            )
