from typer.testing import CliRunner

from reproscout.application import PreflightResult
from reproscout.cli import app
from reproscout.onboarding import (
    DockerInspection,
    HostSnapshot,
    SetupResult,
    SetupStatus,
)
from reproscout.preflight import PreflightArtifactPaths, PreflightStatus

runner = CliRunner()


def test_help_command_exits_successfully() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Audit whether open-source software can be reproduced" in result.stdout
    assert "audit" in result.stdout
    assert "preflight" in result.stdout
    assert "inspect" in result.stdout
    assert "doctor" in result.stdout
    assert "setup" in result.stdout


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "ReproScout 0.1.0"


def test_version_option() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "ReproScout 0.1.0"


def test_run_help_documents_goal_and_machine_output() -> None:
    result = runner.invoke(app, ["run", "--help"])

    assert result.exit_code == 0
    assert "--goal" in result.stdout
    assert "--runs-dir" in result.stdout
    assert "--json" in result.stdout


def test_run_rejects_unknown_goal_before_intake() -> None:
    result = runner.invoke(
        app,
        ["run", "https://github.com/example/project", "--goal", "train"],
    )

    assert result.exit_code == 2
    assert "must be one of" in result.stderr


def test_report_displays_run_artifact(tmp_path) -> None:
    run_directory = tmp_path / "run-001"
    run_directory.mkdir()
    (run_directory / "report.md").write_text("# Fixture report\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["report", "run-001", "--runs-dir", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert result.stdout == "# Fixture report\n"


def test_report_rejects_path_traversal(tmp_path) -> None:
    result = runner.invoke(
        app,
        ["report", "..\\outside", "--runs-dir", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "unsupported path characters" in result.stderr


def test_runs_lists_only_completed_reports(tmp_path) -> None:
    completed = tmp_path / "run-001"
    completed.mkdir()
    (completed / "report.md").write_text("report", encoding="utf-8")
    (tmp_path / "incomplete").mkdir()

    result = runner.invoke(
        app,
        ["runs", "--runs-dir", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0
    assert '"run_id": "run-001"' in result.stdout
    assert "incomplete" not in result.stdout


def test_run_command_rejects_unsupported_url() -> None:
    result = runner.invoke(app, ["run", "https://gitlab.com/example/project"])

    assert result.exit_code == 1
    assert "Only HTTPS GitHub repository URLs are supported" in result.stderr


def test_doctor_json_is_secret_free(monkeypatch) -> None:
    monkeypatch.setattr(
        "reproscout.cli.doctor_report",
        lambda: {
            "docker": {"available": True},
            "runtime_compatibility": "supported",
            "llm_configured": False,
        },
    )
    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    assert "GEMINI_API_KEY" not in result.stdout
    assert "GROQ_API_KEY" not in result.stdout
    assert "llm_configured" in result.stdout


def test_doctor_returns_failure_exit_when_runtime_is_not_ready(monkeypatch) -> None:
    monkeypatch.setattr(
        "reproscout.cli.doctor_report",
        lambda: {
            "docker": {"available": False},
            "runtime_compatibility": "supported",
        },
    )

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 1


def test_setup_dry_run_has_a_success_exit_without_running_actions(monkeypatch) -> None:
    planned = SetupResult(
        status=SetupStatus.NEEDS_SETUP,
        host=HostSnapshot(
            system="Linux",
            release="test",
            version="test",
            machine="x86_64",
            cpu_count=4,
            memory_bytes=1_000,
            disk_free_bytes=2_000,
        ),
        docker=DockerInspection(
            installed=False,
            daemon_running=False,
            command=None,
            server_version=None,
            context=None,
            context_verified=False,
            error="Docker is not installed",
            sandbox_security={"cap_drop_all": "configured"},
        ),
        message="Dry run: no setup command was executed.",
    )
    monkeypatch.setattr("reproscout.cli.run_setup", lambda **kwargs: planned)

    result = runner.invoke(app, ["setup", "--dry-run", "--json"])

    assert result.exit_code == 0
    assert '"status": "NEEDS_SETUP"' in result.stdout


def test_status_rejects_path_traversal_before_database_access(tmp_path) -> None:
    result = runner.invoke(
        app,
        ["status", "..\\outside", "--runs-dir", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "unsupported path characters" in result.stderr


def test_cleanup_rejects_path_traversal_before_docker_access() -> None:
    result = runner.invoke(app, ["cleanup", "--run-id", "..\\outside"])

    assert result.exit_code == 1
    assert "unsupported path characters" in result.stderr


def test_preflight_non_ready_status_returns_failure_exit_code(
    tmp_path,
    monkeypatch,
) -> None:
    paths = PreflightArtifactPaths(
        run_directory=tmp_path,
        environment_report=tmp_path / "environment-report.md",
        environment_evidence=tmp_path / "environment-evidence.json",
        agent_handoff=tmp_path / "agent-handoff.md",
    )

    def fake_preflight(self, request):  # type: ignore[no-untyped-def]
        del self, request
        return PreflightResult(
            run_id="fixture-run",
            repository="example/project",
            commit_sha="a" * 40,
            status=PreflightStatus.INCOMPLETE,
            verified_environment=False,
            artifact_paths=paths,
        )

    monkeypatch.setattr("reproscout.cli.AuditService.preflight", fake_preflight)

    result = runner.invoke(
        app,
        ["preflight", "https://github.com/example/project", "--json"],
    )

    assert result.exit_code == 1
    assert '"status": "INCOMPLETE"' in result.stdout
