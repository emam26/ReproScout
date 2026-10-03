from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from reproscout.analysis import RepositoryAnalyzer
from reproscout.execution import ExecutionFailureKind, PlanExecutionEngine
from reproscout.planning import ReproductionPlanner
from reproscout.repo import CloneResult, build_manifest, parse_github_url
from reproscout.sandbox import (
    DockerSandbox,
    SandboxConfig,
    SandboxError,
    check_docker_available,
)
from reproscout.state import RunOutcome, SQLiteRunStore, Stage


@pytest.fixture(scope="session")
def execution_docker_availability():
    try:
        return check_docker_available()
    except SandboxError as exc:
        pytest.skip(f"Docker integration unavailable: {exc}")


def _container_exists(command: list[str], container_id: str) -> bool:
    result = subprocess.run(
        [
            *command,
            "ps",
            "--all",
            "--filter",
            f"id={container_id}",
            "--format",
            "{{.ID}}",
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    return bool(result.stdout.strip())


def _fixture_manifest(run_directory: Path, files: dict[str, str]):
    repository_path = run_directory / "workspace" / "repository"
    repository_path.mkdir(parents=True)
    for relative, contents in files.items():
        path = repository_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    repository = parse_github_url("https://github.com/example/fixture")
    clone = CloneResult(
        repository_url=repository.normalized_url,
        workspace_path=repository_path,
        commit_sha="b" * 40,
        branch="main",
    )
    return build_manifest(repository, clone), repository_path


def _prepare_pipeline(
    store: SQLiteRunStore,
    manifest,
):
    fixture_name = manifest.workspace_path.parents[1].name
    run = store.create_run(
        run_id=f"docker-{fixture_name}",
        context={
            "commit_sha": manifest.commit_sha,
            "repository": f"{manifest.owner}/{manifest.repository_name}",
        },
    )
    store.transition(run.run_id, Stage.ANALYZE)
    analysis = asyncio.run(RepositoryAnalyzer().analyze(manifest))
    store.transition(run.run_id, Stage.PLAN)
    plan = ReproductionPlanner().create_plan(analysis)
    return run.run_id, plan


@pytest.mark.docker
@pytest.mark.integration
def test_controlled_pipeline_succeeds_only_inside_docker(
    tmp_path: Path,
    execution_docker_availability,
) -> None:
    run_directory = tmp_path / "successful-run"
    manifest, workspace = _fixture_manifest(
        run_directory,
        {
            "README.md": "python app.py\npython second.py\n",
            "app.py": "print('fixture-success')\n",
            "second.py": "import sys\nprint('fixture-second')\nsys.stderr.write('fixture-warning\\n')\n",
        },
    )
    sandboxes: list[DockerSandbox] = []

    def factory(config: SandboxConfig) -> DockerSandbox:
        sandbox = DockerSandbox(
            config,
            docker_command=execution_docker_availability.command,
        )
        sandboxes.append(sandbox)
        return sandbox

    with SQLiteRunStore(run_directory / "state.sqlite3") as store:
        run_id, plan = _prepare_pipeline(store, manifest)
        result = PlanExecutionEngine(store, sandbox_factory=factory).execute(
            plan,
            run_id=run_id,
            run_directory=run_directory,
            workspace=workspace,
        )
        state = store.get_run(run_id)
        events = store.list_events(run_id)

    container_ids = [step.container_id for step in result.steps]
    assert result.workflow_succeeded is True
    assert [step.stdout.strip() for step in result.steps] == [
        "fixture-success",
        "fixture-second",
    ]
    assert "fixture-warning" in result.steps[1].stderr
    assert state.outcome is RunOutcome.SUCCEEDED
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert sandboxes[0].container_id is None
    assert all(container_id is not None for container_id in container_ids)
    assert all(
        not _container_exists(execution_docker_availability.command, container_id)
        for container_id in container_ids
        if container_id is not None
    )


@pytest.mark.docker
@pytest.mark.integration
def test_controlled_pipeline_stops_on_failing_fixture(
    tmp_path: Path,
    execution_docker_availability,
) -> None:
    run_directory = tmp_path / "failing-run"
    manifest, workspace = _fixture_manifest(
        run_directory,
        {
            "README.md": "python fail.py\npython later.py\n",
            "fail.py": "import sys\nsys.stderr.write('expected-failure\\n')\nraise SystemExit(7)\n",
            "later.py": "open('should-not-exist', 'w').write('bad')\n",
        },
    )
    sandboxes: list[DockerSandbox] = []

    def factory(config: SandboxConfig) -> DockerSandbox:
        sandbox = DockerSandbox(
            config,
            docker_command=execution_docker_availability.command,
        )
        sandboxes.append(sandbox)
        return sandbox

    with SQLiteRunStore(run_directory / "state.sqlite3") as store:
        run_id, plan = _prepare_pipeline(store, manifest)
        result = PlanExecutionEngine(store, sandbox_factory=factory).execute(
            plan,
            run_id=run_id,
            run_directory=run_directory,
            workspace=workspace,
        )
        state = store.get_run(run_id)

    assert result.workflow_succeeded is False
    assert result.failure_kind is ExecutionFailureKind.COMMAND_NONZERO
    assert result.steps[0].exit_code == 7
    assert "expected-failure" in result.steps[0].stderr
    assert len(result.steps) == 1
    assert not (workspace / "should-not-exist").exists()
    assert state.outcome is RunOutcome.FAILED
    assert sandboxes[0].container_id is None


@pytest.mark.docker
@pytest.mark.integration
def test_controlled_pipeline_captures_timeout(
    tmp_path: Path,
    execution_docker_availability,
) -> None:
    run_directory = tmp_path / "timeout-run"
    manifest, workspace = _fixture_manifest(
        run_directory,
        {
            "README.md": "python slow.py\n",
            "slow.py": "import time\ntime.sleep(30)\n",
        },
    )

    def factory(config: SandboxConfig) -> DockerSandbox:
        return DockerSandbox(
            config,
            docker_command=execution_docker_availability.command,
        )

    with SQLiteRunStore(run_directory / "state.sqlite3") as store:
        run_id, plan = _prepare_pipeline(store, manifest)
        timed_step = plan.steps[0].model_copy(update={"timeout_seconds": 1})
        plan = plan.model_copy(update={"steps": [timed_step]})
        result = PlanExecutionEngine(store, sandbox_factory=factory).execute(
            plan,
            run_id=run_id,
            run_directory=run_directory,
            workspace=workspace,
        )

    assert result.workflow_succeeded is False
    assert result.failure_kind is ExecutionFailureKind.TIMEOUT
    assert result.steps[0].timed_out is True
    assert result.steps[0].exit_code == 124
