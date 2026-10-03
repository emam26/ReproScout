from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from reproscout.diagnostics import EnvironmentFingerprint
from reproscout.execution import StepExecutionResult
from reproscout.reporting import (
    ReportArtifactPaths,
    ReportAttempt,
    ReportAttemptSource,
    ReportError,
    RunReport,
    RunReportWriter,
)
from reproscout.state import SQLiteRunStore, Stage


def _environment() -> EnvironmentFingerprint:
    return EnvironmentFingerprint(
        repository_commit_sha="a" * 40,
        container_image="python:3.11-slim",
        os_name="Linux",
        os_release="fixture",
        architecture="x86_64",
        python_version="3.11.0",
        installed_packages=[],
        cpu_allocation=1,
        memory_limit="512m",
        gpu_visible=False,
        environment_variable_names=[],
        network_mode="none",
    )


def _command() -> StepExecutionResult:
    now = datetime.now(UTC)
    return StepExecutionResult(
        step_id="step-001",
        attempt_number=1,
        action_type="RUN_DEMO",
        command="python app.py",
        working_directory=".",
        started_at=now,
        finished_at=now,
        duration=0.1,
        stdout="ok\n",
        stderr="",
        exit_code=0,
        timed_out=False,
    )


def _report() -> RunReport:
    return RunReport(
        run_id="report-run",
        repository="example/project",
        commit_sha="a" * 40,
        goal="Run the documented demo.",
        documented_setup=["python -m pip install -r requirements.txt"],
        initial_attempt=ReportAttempt(
            attempt_id="attempt-001",
            source=ReportAttemptSource.OFFICIAL_DOCUMENTED,
            description="Initial documented attempt.",
            commands=["python app.py"],
            workflow_succeeded=False,
        ),
        agent_assisted_attempts=[
            ReportAttempt(
                attempt_id="attempt-002",
                source=ReportAttemptSource.AGENT_ASSISTED,
                description="One bounded repair retry.",
                commands=["python app.py"],
                workflow_succeeded=True,
            )
        ],
        blockers=["No clean-room rerun was supplied."],
        documentation_gaps=["The README omits the required output file."],
    )


def test_report_writer_emits_real_schema_versioned_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        run = store.create_run(run_id="report-run")
        store.transition(run.run_id, Stage.ANALYZE)
        events = store.list_events(run.run_id)

    paths = RunReportWriter(run_dir).write(
        _report(),
        events=events,
        commands=[_command()],
        environment=_environment(),
        patches_diff="--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n",
        recipe_commands=["python app.py"],
    )

    assert isinstance(paths, ReportArtifactPaths)
    expected = {
        "run.json",
        "report.md",
        "events.jsonl",
        "commands.jsonl",
        "environment.json",
        "patches.diff",
        "reproduce.sh",
    }
    assert set(paths.written_files) == expected
    payload = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0"
    markdown = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "Official documented reproduction" in markdown
    assert "Agent-assisted reproduction" in markdown
    first_event = json.loads(
        (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert first_event["sequence"] == 1
    assert (run_dir / "commands.jsonl").is_file()
    assert (run_dir / "environment.json").is_file()
    assert (run_dir / "patches.diff").is_file()
    assert (
        (run_dir / "reproduce.sh")
        .read_text(encoding="utf-8")
        .endswith("python app.py\n")
    )


def test_report_writer_does_not_create_optional_artifacts_without_data(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    paths = RunReportWriter(run_dir).write(_report())

    assert paths.written_files == ["run.json", "report.md"]
    assert not (run_dir / "events.jsonl").exists()
    assert not (run_dir / "environment.json").exists()
    assert not (run_dir / "reproduce.sh").exists()


def test_report_writer_rejects_secret_recipe_and_secret_report_text(
    tmp_path: Path,
) -> None:
    with pytest.raises(ReportError):
        RunReportWriter(tmp_path / "recipe").write(
            _report(),
            recipe_commands=["GITHUB_TOKEN=literal-secret python app.py"],
        )

    unsafe = _report().model_copy(update={"blockers": ["GITHUB_TOKEN=literal-secret"]})
    with pytest.raises(ReportError):
        RunReportWriter(tmp_path / "report").write(unsafe)
