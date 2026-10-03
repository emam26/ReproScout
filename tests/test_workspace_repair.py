from __future__ import annotations

import os
from pathlib import Path

import pytest

from reproscout.diagnostics import EvidenceBuilder, normalize_failure
from reproscout.execution import PlanExecutionEngine
from reproscout.planning import PlanActionType, PlanBaseline, PlanStep, ReproductionPlan
from reproscout.repair import (
    ControlledRepairPipeline,
    RepairAction,
    RepairActionType,
    RepairExperimentStatus,
    Reversibility,
    WorkspaceEditConflictError,
    WorkspaceEditError,
    WorkspaceEditLimits,
    WorkspaceEditor,
)
from reproscout.sandbox import ExecutionResult, Sandbox, SandboxConfig
from reproscout.state import RunOutcome, SQLiteRunStore, Stage


def _patch(old: str, new: str) -> str:
    return (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1 +1 @@\n"
        f"-{old}\n+{new}\n"
    )


def test_replace_read_hash_diff_and_exact_rollback(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    audit = tmp_path / "run"
    workspace.mkdir()
    (workspace / "app.py").write_text('print("broken")\n', encoding="utf-8")
    editor = WorkspaceEditor(workspace, audit_directory=audit)

    before = editor.read_file("app.py")
    result = editor.replace_text("app.py", 'print("broken")', 'print("fixed")')

    assert before.content == 'print("broken")' + os.linesep
    assert result.changes[0].before.sha256 == before.sha256
    assert result.changes[0].after.sha256 != before.sha256
    assert 'print("fixed")' in result.patches_diff
    artifact = editor.write_patch_artifact()
    assert artifact == audit / "patches.diff"
    assert artifact.read_bytes().decode("utf-8") == result.patches_diff
    assert (workspace / "app.py").read_text(encoding="utf-8") == 'print("fixed")\n'

    editor.rollback()

    assert (workspace / "app.py").read_text(encoding="utf-8") == 'print("broken")\n'
    assert editor.changed_files == ()


def test_unified_patch_is_bounded_and_reversible(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text('print("broken")\n', encoding="utf-8")
    editor = WorkspaceEditor(workspace)

    result = editor.apply_patch(_patch('print("broken")', 'print("fixed")'))
    assert result.operation == "apply_patch"
    assert (workspace / "app.py").read_text(encoding="utf-8") == 'print("fixed")\n'
    editor.rollback()
    assert (workspace / "app.py").read_text(encoding="utf-8") == 'print("broken")\n'


def test_workspace_edit_preserves_executable_file_mode(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    script = workspace / "run.sh"
    script.write_text("#!/bin/sh\necho old\n", encoding="utf-8")
    try:
        script.chmod(0o755)
    except OSError:
        pytest.skip("file mode changes are unavailable in this environment")
    if script.stat().st_mode & 0o111 == 0:
        pytest.skip("executable mode bits are unavailable in this environment")
    WorkspaceEditor(workspace).replace_text("run.sh", "old", "new")
    assert script.stat().st_mode & 0o777 == 0o755


@pytest.mark.parametrize(
    "path", ["../outside.txt", "/tmp/outside.txt", "C:\\outside.txt", ".git/config"]
)
def test_workspace_paths_cannot_escape_or_edit_git_metadata(
    tmp_path: Path, path: str
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("safe\n", encoding="utf-8")
    editor = WorkspaceEditor(workspace)

    with pytest.raises(WorkspaceEditError):
        editor.read_file(path)


def test_symlink_escape_and_project_root_are_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    link = workspace / "link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable in this environment")
    editor = WorkspaceEditor(workspace)
    with pytest.raises(WorkspaceEditError):
        editor.read_file("link.txt")

    with pytest.raises(WorkspaceEditError, match="own project root"):
        WorkspaceEditor(Path(__file__).resolve().parents[1])


def test_hash_conflict_size_limits_changed_file_limits_and_sensitive_patch(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("old\n", encoding="utf-8")
    editor = WorkspaceEditor(workspace)
    digest = editor.read_file("app.py").sha256
    (workspace / "app.py").write_text("external\n", encoding="utf-8")

    with pytest.raises(WorkspaceEditConflictError):
        editor.replace_text("app.py", "external", "new", expected_sha256=digest)

    limited = WorkspaceEditor(
        workspace,
        limits=WorkspaceEditLimits(
            max_file_bytes=1_024, max_patch_characters=100, max_changed_files=1
        ),
    )
    with pytest.raises(WorkspaceEditError, match="bound"):
        limited.apply_patch("x" * 101)
    with pytest.raises(WorkspaceEditError, match="credential"):
        limited.apply_patch(_patch("external", "API_KEY=secret"))


class _RepairAwareSandbox(Sandbox):
    def __init__(self, config: SandboxConfig) -> None:
        self.config = config

    def create(self) -> None:
        return None

    def execute(
        self, command: str, *, timeout_seconds: float | None = None
    ) -> ExecutionResult:
        content = (self.config.workspace_path / "app.py").read_text(encoding="utf-8")
        if "fixed" in content:
            return ExecutionResult(
                command=command,
                stdout="fixed\n",
                stderr="",
                exit_code=0,
                duration=0.01,
                timed_out=False,
                container_id="fixture-container",
            )
        return ExecutionResult(
            command=command,
            stdout="",
            stderr="expected fixed marker\n",
            exit_code=1,
            duration=0.01,
            timed_out=False,
            container_id="fixture-container",
        )

    def destroy(self) -> None:
        return None


def _plan() -> ReproductionPlan:
    return ReproductionPlan(
        repository="fixture/repairable",
        commit_sha="a" * 40,
        goal="Run the fixture.",
        baseline=PlanBaseline.OFFICIAL_DOCUMENTATION,
        steps=[
            PlanStep(
                step_id="step-001",
                action_type=PlanActionType.RUN_DEMO,
                command="python app.py",
                purpose="Run the fixture.",
                timeout_seconds=20,
                expected_outcome="The fixture exits successfully.",
            )
        ],
        overall_timeout_seconds=60,
    )


def _planned_run(store: SQLiteRunStore) -> str:
    run = store.create_run(context={"repository": "fixture/repairable"})
    store.transition(run.run_id, Stage.ANALYZE)
    store.transition(run.run_id, Stage.PLAN)
    return run.run_id


def test_controlled_pipeline_executes_repairable_fixture_and_rolls_back(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    run_directory = tmp_path / "run"
    workspace.mkdir()
    (workspace / "app.py").write_text('print("broken")\n', encoding="utf-8")
    factory = lambda config: _RepairAwareSandbox(config)

    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        run_id = _planned_run(store)
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
            action = RepairAction(
                action_id="repair-001",
                action_type=RepairActionType.APPLY_MINIMAL_PATCH,
                reason="The fixture has a documented one-line compatibility repair.",
                supporting_evidence=[context.evidence[0].reference],
                expected_effect="The fixture should exit successfully.",
                risk="LOW",
                reversibility=Reversibility.ROLLBACK_REQUIRED,
                arguments={"patch": _patch('print("broken")', 'print("fixed")')},
            )
            return context, action

        result = ControlledRepairPipeline(store, execution).run(
            _plan(),
            run_id=run_id,
            run_directory=run_directory,
            workspace=workspace,
            diagnose=diagnose,
        )
        state = store.get_run(run_id)

    assert result.initial.workflow_succeeded is False
    assert result.retry is not None and result.retry.workflow_succeeded is True
    assert result.repair is not None
    assert result.repair.status is RepairExperimentStatus.IMPROVED
    assert (run_directory / "patches.diff").is_file()
    assert (workspace / "app.py").read_text(encoding="utf-8") == 'print("broken")\n'
    assert state.stage is Stage.DEBUG
    assert state.outcome is None


def test_controlled_pipeline_does_not_claim_success_without_repair_or_verification(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    run_directory = tmp_path / "run"
    workspace.mkdir()
    (workspace / "app.py").write_text('print("broken")\n', encoding="utf-8")

    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        run_id = _planned_run(store)
        result = ControlledRepairPipeline(
            store,
            PlanExecutionEngine(
                store, sandbox_factory=lambda config: _RepairAwareSandbox(config)
            ),
        ).run(
            _plan(),
            run_id=run_id,
            run_directory=run_directory,
            workspace=workspace,
            diagnose=lambda _: (_diagnostic_context(), None),
        )
        state = store.get_run(run_id)

    assert result.initial.workflow_succeeded is False
    assert result.repair is None
    assert state.stage is Stage.DEBUG
    assert state.outcome is None
    assert state.outcome is not RunOutcome.SUCCEEDED


def _diagnostic_context():
    failure = normalize_failure("", "unrepairable", exit_code=1, timed_out=False)
    return EvidenceBuilder().build(failure, command="python app.py")
