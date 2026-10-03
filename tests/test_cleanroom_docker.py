from __future__ import annotations

from pathlib import Path

import pytest

from reproscout.cleanroom import CleanRoomRunner, recipe_from_plan
from reproscout.execution import PlanExecutionEngine
from reproscout.planning import (
    PlanActionType,
    PlanBaseline,
    PlanStep,
    ReproductionPlan,
)
from reproscout.sandbox import (
    DockerSandbox,
    SandboxConfig,
    SandboxError,
    check_docker_available,
)
from reproscout.state import SQLiteRunStore
from reproscout.status import ReproductionStatus


@pytest.fixture(scope="session")
def cleanroom_docker_availability():
    try:
        return check_docker_available()
    except SandboxError as exc:
        pytest.skip(f"Docker integration unavailable: {exc}")


def _plan() -> ReproductionPlan:
    return ReproductionPlan(
        repository="fixture/clean-room",
        commit_sha="d" * 40,
        goal="Reproduce the clean-room fixture.",
        baseline=PlanBaseline.OFFICIAL_DOCUMENTATION,
        steps=[
            PlanStep(
                step_id="step-001",
                action_type=PlanActionType.RUN_DEMO,
                command="python app.py",
                purpose="Run the clean-room fixture.",
                timeout_seconds=30,
                expected_outcome="The clean-room fixture exits successfully.",
            )
        ],
        overall_timeout_seconds=90,
    )


def _factory(availability):
    def factory(config: SandboxConfig) -> DockerSandbox:
        return DockerSandbox(config, docker_command=availability.command)

    return factory


@pytest.mark.docker
@pytest.mark.integration
def test_clean_room_reproduces_from_fresh_workspace_and_recipe(
    tmp_path: Path,
    cleanroom_docker_availability,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    original = "import sys\nraise SystemExit(7)\n"
    source.joinpath("app.py").write_text(original, encoding="utf-8")
    patch = (
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -2 +2 @@\n"
        "-raise SystemExit(7)\n"
        "+print('clean-room success')\n"
    )
    plan = _plan()
    recipe = recipe_from_plan(plan, patches_diff=patch)

    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        result = CleanRoomRunner(
            store,
            PlanExecutionEngine(
                store,
                sandbox_factory=_factory(cleanroom_docker_availability),
            ),
        ).run(
            recipe,
            source_workspace=source,
            run_directory=tmp_path / "runs",
        )

    assert result.execution.workflow_succeeded is True
    assert result.verification.status.value == "PASSED"
    assert result.status.status is ReproductionStatus.REPRODUCED
    assert source.joinpath("app.py").read_text(encoding="utf-8") == original
    assert (
        Path(result.clean_workspace).joinpath("app.py").read_text(encoding="utf-8")
        != original
    )
    clean_run_directory = Path(result.clean_workspace).parent
    assert clean_run_directory.joinpath("reproduce.sh").is_file()
    assert clean_run_directory.joinpath("REPRODUCTION.md").is_file()
    assert clean_run_directory.joinpath("patches.diff").is_file()


@pytest.mark.docker
@pytest.mark.integration
def test_clean_room_failure_is_not_claimed_as_reproduced(
    tmp_path: Path,
    cleanroom_docker_availability,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    source.joinpath("app.py").write_text(
        "raise SystemExit(9)\n",
        encoding="utf-8",
    )
    recipe = recipe_from_plan(_plan())

    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        result = CleanRoomRunner(
            store,
            PlanExecutionEngine(
                store,
                sandbox_factory=_factory(cleanroom_docker_availability),
            ),
        ).run(
            recipe,
            source_workspace=source,
            run_directory=tmp_path / "runs",
        )

    assert result.execution.workflow_succeeded is False
    assert result.verification.status.value == "EXECUTION_FAILED"
    assert result.status.status is ReproductionStatus.FAILED
