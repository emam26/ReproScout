from __future__ import annotations

from pathlib import Path

import pytest

from reproscout.diagnostics import EvidenceBuilder, normalize_failure
from reproscout.execution import PlanExecutionEngine
from reproscout.planning import (
    PlanActionType,
    PlanBaseline,
    PlanStep,
    ReproductionPlan,
)
from reproscout.repair import (
    ControlledRepairPipeline,
    RepairAction,
    RepairActionType,
    RepairExperimentStatus,
    Reversibility,
)
from reproscout.sandbox import (
    DockerSandbox,
    SandboxConfig,
    SandboxError,
    check_docker_available,
)
from reproscout.state import SQLiteRunStore, Stage


@pytest.fixture(scope="session")
def repair_docker_availability():
    try:
        return check_docker_available()
    except SandboxError as exc:
        pytest.skip(f"Docker integration unavailable: {exc}")


def _plan() -> ReproductionPlan:
    return ReproductionPlan(
        repository="fixture/docker-repairable",
        commit_sha="c" * 40,
        goal="Run the repaired Docker fixture.",
        baseline=PlanBaseline.OFFICIAL_DOCUMENTATION,
        steps=[
            PlanStep(
                step_id="step-001",
                action_type=PlanActionType.RUN_DEMO,
                command="python app.py",
                purpose="Run the Docker repair fixture.",
                timeout_seconds=30,
                expected_outcome="The fixture exits successfully.",
            )
        ],
        overall_timeout_seconds=90,
    )


@pytest.mark.docker
@pytest.mark.integration
def test_repairable_fixture_retries_inside_docker_and_rolls_back(
    tmp_path: Path,
    repair_docker_availability,
) -> None:
    run_directory = tmp_path / "run"
    workspace = run_directory / "workspace"
    workspace.mkdir(parents=True)
    original = "import sys\nraise SystemExit(7)\n"
    workspace.joinpath("app.py").write_text(original, encoding="utf-8")
    patch = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -2 +2 @@\n"
        "-raise SystemExit(7)\n"
        "+print('fixed in docker')\n"
    )

    def factory(config: SandboxConfig) -> DockerSandbox:
        return DockerSandbox(
            config,
            docker_command=repair_docker_availability.command,
        )

    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        run = store.create_run(run_id=f"docker-repair-{tmp_path.name}")
        store.transition(run.run_id, Stage.ANALYZE)
        store.transition(run.run_id, Stage.PLAN)
        execution = PlanExecutionEngine(store, sandbox_factory=factory)

        def diagnose(initial):
            failed = initial.steps[0]
            failure = normalize_failure(
                failed.stdout,
                failed.stderr,
                exit_code=failed.exit_code,
                timed_out=failed.timed_out,
            )
            context = EvidenceBuilder().build(failure, command=failed.command or "")
            return context, RepairAction(
                action_id="repair-001",
                action_type=RepairActionType.APPLY_MINIMAL_PATCH,
                reason="The bounded fixture patch removes the intentional failure.",
                supporting_evidence=[context.evidence[0].reference],
                expected_effect="The Docker retry exits successfully.",
                risk="LOW",
                reversibility=Reversibility.ROLLBACK_REQUIRED,
                arguments={"patch": patch},
            )

        result = ControlledRepairPipeline(store, execution).run(
            _plan(),
            run_id=run.run_id,
            run_directory=run_directory,
            workspace=workspace,
            diagnose=diagnose,
        )
        state = store.get_run(run.run_id)

    assert result.initial.workflow_succeeded is False
    assert result.retry is not None and result.retry.workflow_succeeded is True
    assert result.repair is not None
    assert result.repair.status is RepairExperimentStatus.IMPROVED
    assert workspace.joinpath("app.py").read_text(encoding="utf-8") == original
    assert run_directory.joinpath("patches.diff").is_file()
    assert state.stage is Stage.DEBUG
    assert state.outcome is None
