from typer.testing import CliRunner

from reproscout.cli import app

runner = CliRunner()


def test_help_command_exits_successfully() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Audit whether open-source software can be reproduced" in result.stdout
    assert "audit" in result.stdout
    assert "inspect" in result.stdout
    assert "doctor" in result.stdout


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


def test_doctor_json_is_secret_free() -> None:
    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    assert "GEMINI_API_KEY" not in result.stdout
    assert "GROQ_API_KEY" not in result.stdout
    assert "llm_configured" in result.stdout


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
