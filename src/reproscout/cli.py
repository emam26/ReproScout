"""Small public command-line interface for local reproducibility audits."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Annotated

import typer

from . import __version__
from .application import (
    AuditRequest,
    AuditService,
    PreflightRequest,
    ServiceError,
)
from .application.service import doctor_report
from .config import get_settings
from .onboarding import SetupStatus, run_setup
from .preflight import PreflightStatus
from .repo.clone import (
    CloneError,
    RunWorkspaceError,
    clone_repository,
    create_run_workspace,
)
from .repo.manifest import ManifestError, build_manifest
from .repo.urls import RepositoryUrlError, parse_github_url
from .sandbox.docker import resolve_docker_command
from .security import redact_sensitive_text
from .state import RunNotFoundError, SQLiteRunStore

_SUPPORTED_GOALS = {"auto", "install", "tests", "demo"}
_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_SAFE_CONTAINER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")

app = typer.Typer(
    name="reproscout",
    help="Audit whether open-source software can be reproduced.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"ReproScout {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show the installed ReproScout version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Local reproducibility auditing commands."""


@app.command()
def version() -> None:
    """Print the ReproScout version."""

    typer.echo(f"ReproScout {__version__}")


@app.command()
def audit(
    repository_url: Annotated[str, typer.Argument(help="HTTPS GitHub repository URL.")],
    goal: Annotated[
        str,
        typer.Option("--goal", help="Target workflow: auto, install, tests, or demo."),
    ] = "auto",
    runs_dir: Annotated[
        Path | None,
        typer.Option(
            "--runs-dir", help="Directory for run state and artifacts.", path_type=Path
        ),
    ] = None,
    no_ai: Annotated[
        bool,
        typer.Option(
            "--no-ai", help="Disable optional LLM interpretation and diagnosis."
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit one machine-readable JSON result."),
    ] = False,
) -> None:
    """Run intake, analysis, Docker execution, verification, and reporting."""

    normalized_goal = goal.strip().lower()
    if normalized_goal not in _SUPPORTED_GOALS:
        raise typer.BadParameter(
            f"must be one of: {', '.join(sorted(_SUPPORTED_GOALS))}",
            param_hint="--goal",
        )
    try:
        result = AuditService(runs_dir).audit(
            AuditRequest(
                repository_url=repository_url,
                goal=normalized_goal,
                no_ai=no_ai,
            )
        )
    except (ServiceError, ValueError) as exc:
        _error(str(exc), json_output=json_output)
        raise typer.Exit(code=1) from exc
    payload = result.model_dump(mode="json")
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    typer.echo("Reproducibility audit complete.\n")
    typer.echo(f"Run ID: {result.run_id}")
    typer.echo(f"Status: {result.status.status.value}")
    typer.echo(f"Verification: {result.status.verification_level or 'unavailable'}")
    typer.echo(f"Attempts: {result.attempts}")
    typer.echo(f"Repairs: {result.repairs}")
    typer.echo(f"Report: {result.report_path}")
    if result.reproduction_package_path is not None:
        typer.echo(f"Reproduction package: {result.reproduction_package_path}")


@app.command()
def preflight(
    repository_url: Annotated[str, typer.Argument(help="HTTPS GitHub repository URL.")],
    runs_dir: Annotated[
        Path | None,
        typer.Option(
            "--runs-dir",
            help="Directory for preflight state and artifacts.",
            path_type=Path,
        ),
    ] = None,
    ai: Annotated[
        bool,
        typer.Option(
            "--ai",
            help="Enable optional Gemini/Groq interpretation and diagnosis.",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit one machine-readable JSON result."),
    ] = False,
) -> None:
    """Prepare and verify an environment without running project workflows."""

    try:
        result = AuditService(runs_dir).preflight(
            PreflightRequest(repository_url=repository_url, no_ai=not ai)
        )
    except (ServiceError, ValueError) as exc:
        _error(str(exc), json_output=json_output)
        raise typer.Exit(code=1) from exc
    payload = result.model_dump(mode="json")
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        typer.echo("Environment preflight complete.\n")
        typer.echo(f"Run ID: {result.run_id}")
        typer.echo(f"Status: {result.status.value}")
        verified = "yes" if result.verified_environment else "no"
        typer.echo(f"Verified environment: {verified}")
        typer.echo(f"Environment report: {result.artifact_paths.environment_report}")
        typer.echo(f"Evidence: {result.artifact_paths.environment_evidence}")
        typer.echo(f"Agent handoff: {result.artifact_paths.agent_handoff}")
    if result.status is not PreflightStatus.READY:
        raise typer.Exit(code=1)


@app.command()
def inspect(
    repository_url: Annotated[str, typer.Argument(help="HTTPS GitHub repository URL.")],
    runs_dir: Annotated[
        Path | None,
        typer.Option(
            "--runs-dir", help="Directory for the inspection workspace.", path_type=Path
        ),
    ] = None,
    no_ai: Annotated[
        bool,
        typer.Option("--no-ai", help="Disable optional LLM interpretation."),
    ] = True,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit one machine-readable JSON result."),
    ] = False,
) -> None:
    """Inspect repository structure and workflow without executing target code."""

    try:
        result = AuditService(runs_dir).inspect(
            AuditRequest(repository_url=repository_url, no_ai=no_ai)
        )
    except (ServiceError, ValueError) as exc:
        _error(str(exc), json_output=json_output)
        raise typer.Exit(code=1) from exc
    payload = result.model_dump(mode="json")
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    typer.echo(f"Repository: {result.manifest.owner}/{result.manifest.repository_name}")
    typer.echo(f"Commit: {result.manifest.commit_sha}")
    typer.echo(f"Project type: {result.analysis.project_type}")
    typer.echo(f"Install commands: {len(result.analysis.install_commands)}")
    typer.echo(f"Test commands: {len(result.analysis.test_commands)}")
    typer.echo(f"Run commands: {len(result.analysis.run_commands)}")
    typer.echo(f"Inspection workspace: {result.run_directory}")


@app.command()
def doctor(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit one machine-readable JSON result."),
    ] = False,
) -> None:
    """Check local runtime prerequisites without changing the host."""

    payload = doctor_report()
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        host = payload["host"]
        assert isinstance(host, dict)
        typer.echo(f"Host: {host['system']} {host['release']} ({host['architecture']})")
        typer.echo(f"CPU count: {host['cpu_count'] or 'unavailable'}")
        typer.echo(f"Memory bytes: {host['memory_bytes'] or 'unavailable'}")
        typer.echo(f"Disk free bytes: {host['disk_free_bytes'] or 'unavailable'}")
        typer.echo(f"Python: {payload['python']}")
        typer.echo(f"Git available: {'yes' if payload['git_available'] else 'no'}")
        typer.echo(f"Runtime compatibility: {payload['runtime_compatibility']}")
        virtualization = payload["virtualization"]
        assert isinstance(virtualization, dict)
        typer.echo(f"Virtualization: {virtualization['status']}")
        docker = payload["docker"]
        assert isinstance(docker, dict)
        typer.echo(f"Docker installed: {'yes' if docker['installed'] else 'no'}")
        typer.echo(f"Docker daemon: {'yes' if docker['daemon_running'] else 'no'}")
        typer.echo(f"Docker context: {docker['context'] or 'unverified'}")
        if docker["available"]:
            typer.echo(f"Docker server: {docker['server_version']}")
        if docker.get("error"):
            typer.echo(f"Docker detail: {docker['error']}")
        remediation = payload["remediation"]
        assert isinstance(remediation, list)
        for item in remediation:
            typer.echo(f"Next step: {item}")
        typer.echo(f"Runs directory: {payload['runs_dir']}")
        typer.echo(
            "Runs directory writable: "
            f"{'yes' if payload['runs_dir_writable'] else 'no'}"
        )
        typer.echo(f"LLM provider: {payload['llm_provider']}")
        typer.echo(f"LLM configured: {'yes' if payload['llm_configured'] else 'no'}")
    docker_status = payload["docker"]
    assert isinstance(docker_status, dict)
    if (
        not docker_status["available"]
        or payload["runtime_compatibility"] == "unsupported"
    ):
        raise typer.Exit(code=1)


@app.command()
def setup(
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            help="Authorize the displayed trusted package-manager actions.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help=(
                "Print proposed actions without changing the host or probing Docker."
            ),
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit one machine-readable JSON result."),
    ] = False,
) -> None:
    """Detect and, with explicit consent, configure a supported Docker runtime."""

    planned = run_setup(dry_run=True)
    if planned.actions:
        if json_output and not (yes or dry_run):
            _error(
                "Use --yes for non-interactive JSON setup or omit --json to confirm.",
                json_output=True,
            )
            raise typer.Exit(code=2)
        if not json_output:
            typer.echo("Proposed setup actions:")
            for index, action in enumerate(planned.actions, start=1):
                command = " ".join(action.command)
                suffix = (
                    " (administrative permission may be required)"
                    if action.requires_admin
                    else ""
                )
                typer.echo(f"  {index}. {command}{suffix}")
        if not (yes or dry_run):
            yes = typer.confirm("Run these actions now?", default=False)
    if dry_run:
        result = planned
    elif yes:
        result = run_setup(consent=True)
    else:
        result = run_setup(consent=False)

    if json_output:
        typer.echo(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))
    else:
        typer.echo(f"Setup status: {result.status.value}")
        typer.echo(result.message)
        if result.completed_actions:
            typer.echo(f"Completed actions: {len(result.completed_actions)}")
        if result.output:
            typer.echo("Installer diagnostics:")
            for item in result.output:
                typer.echo(item)
    if result.status is not SetupStatus.READY and not dry_run:
        raise typer.Exit(code=1)


@app.command()
def run(
    repository_url: Annotated[
        str, typer.Argument(help="URL of the repository to inspect.")
    ],
    goal: Annotated[
        str,
        typer.Option("--goal", help="Target workflow: auto, install, tests, or demo."),
    ] = "auto",
    runs_dir: Annotated[
        Path | None,
        typer.Option(
            "--runs-dir",
            help="Directory in which to create the intake workspace.",
            path_type=Path,
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit one machine-readable JSON result."),
    ] = False,
) -> None:
    """Clone a repository and generate its structural manifest only.

    This compatibility command never installs dependencies or executes target code;
    use ``audit`` for the complete bounded workflow.
    """

    normalized_goal = goal.strip().lower()
    if normalized_goal not in _SUPPORTED_GOALS:
        raise typer.BadParameter(
            f"must be one of: {', '.join(sorted(_SUPPORTED_GOALS))}",
            param_hint="--goal",
        )
    try:
        repository = parse_github_url(repository_url)
        configured_runs_dir = (
            runs_dir if runs_dir is not None else get_settings().runs_dir
        )
        run_workspace = create_run_workspace(configured_runs_dir)
        clone = clone_repository(
            repository.normalized_url, run_workspace.repository_path
        )
        manifest = build_manifest(repository, clone)
    except (CloneError, ManifestError, RepositoryUrlError, RunWorkspaceError) as exc:
        _error(f"Repository intake failed: {exc}", json_output=json_output)
        raise typer.Exit(code=1) from exc
    payload = {
        "command": "run",
        "goal": normalized_goal,
        "manifest": manifest.model_dump(mode="json"),
        "run_id": run_workspace.run_id,
        "run_directory": str(run_workspace.run_dir),
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    typer.echo("Repository intake complete.\n")
    typer.echo(f"Goal: {normalized_goal}")
    typer.echo(f"Repository: {manifest.owner}/{manifest.repository_name}")
    typer.echo(f"Commit: {manifest.commit_sha}")
    typer.echo(f"Branch: {manifest.branch or '(detached)'}\n")
    typer.echo("Detected:")
    typer.echo(f"  README files       {len(manifest.documentation_files)}")
    typer.echo(f"  Dependency files   {len(manifest.dependency_files)}")
    typer.echo(f"  Tests              {'yes' if manifest.has_tests else 'no'}")
    typer.echo(f"  Dockerfile         {'yes' if manifest.has_dockerfile else 'no'}")
    typer.echo(f"  CI workflows       {'yes' if manifest.has_ci_workflows else 'no'}\n")
    typer.echo(f"Workspace: {manifest.workspace_path}")


@app.command("runs")
def list_runs(
    runs_dir: Annotated[
        Path | None,
        typer.Option(
            "--runs-dir", help="Directory containing run artifacts.", path_type=Path
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit one machine-readable JSON result."),
    ] = False,
) -> None:
    """List persisted audit runs, including active runs when state is available."""

    root = (
        (runs_dir if runs_dir is not None else get_settings().runs_dir)
        .expanduser()
        .resolve()
    )
    rows: list[dict[str, object]] = []
    database = root / "state.sqlite3"
    if database.is_file():
        try:
            with SQLiteRunStore(database) as store:
                for state in store.list_runs():
                    report_path = _display_report_path(root, state.run_id)
                    rows.append(
                        {
                            "run_id": state.run_id,
                            "stage": state.stage.value,
                            "outcome": state.outcome.value if state.outcome else None,
                            "report": str(report_path)
                            if report_path is not None
                            else None,
                        }
                    )
        except OSError as exc:
            _error(f"Could not list runs: {exc}", json_output=json_output)
            raise typer.Exit(code=1) from exc
    else:
        try:
            entries = sorted(
                entry
                for entry in root.iterdir()
                if entry.is_dir()
                and not entry.is_symlink()
                and _SAFE_RUN_ID.fullmatch(entry.name)
                and _display_report_path(root, entry.name) is not None
            )
        except OSError as exc:
            _error(f"Could not list runs: {exc}", json_output=json_output)
            raise typer.Exit(code=1) from exc
        rows = [
            {
                "run_id": entry.name,
                "report": str(_display_report_path(root, entry.name)),
            }
            for entry in entries
        ]
    if json_output:
        typer.echo(json.dumps(rows, ensure_ascii=False, sort_keys=True))
    elif not rows:
        typer.echo("No runs found.")
    else:
        for row in rows:
            suffix = f"  {row.get('stage', '')}" if row.get("stage") else ""
            typer.echo(f"{row['run_id']}{suffix}")


@app.command()
def status(
    run_id: Annotated[str, typer.Argument(help="Run identifier.")],
    runs_dir: Annotated[
        Path | None,
        typer.Option(
            "--runs-dir", help="Directory containing run state.", path_type=Path
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit one machine-readable JSON result."),
    ] = False,
) -> None:
    """Display persisted control-plane state for one run."""

    root = (
        (runs_dir if runs_dir is not None else get_settings().runs_dir)
        .expanduser()
        .resolve()
    )
    _validate_run_id(run_id)
    try:
        with SQLiteRunStore(root / "state.sqlite3") as store:
            state = store.get_run(run_id)
            events = store.list_events(run_id)
    except (RunNotFoundError, OSError) as exc:
        _error(f"Run not found: {run_id}", json_output=json_output)
        raise typer.Exit(code=1) from exc
    report_path = _display_report_path(root, run_id)
    payload = {
        "run_id": state.run_id,
        "stage": state.stage.value,
        "outcome": state.outcome.value if state.outcome else None,
        "created_at": state.created_at.isoformat(),
        "updated_at": state.updated_at.isoformat(),
        "event_count": len(events),
        "report": str(report_path) if report_path is not None else None,
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        typer.echo(f"Run: {run_id}")
        typer.echo(f"Stage: {state.stage.value}")
        typer.echo(f"Outcome: {state.outcome.value if state.outcome else 'active'}")
        typer.echo(f"Events: {len(events)}")
        if payload["report"]:
            typer.echo(f"Report: {payload['report']}")


@app.command()
def report(
    run_id: Annotated[
        str, typer.Argument(help="Run identifier whose report should be displayed.")
    ],
    runs_dir: Annotated[
        Path | None,
        typer.Option(
            "--runs-dir", help="Directory containing run artifacts.", path_type=Path
        ),
    ] = None,
) -> None:
    """Display a completed run's human-readable report."""

    run_directory = _resolve_run_directory(run_id, runs_dir)
    report_path = _display_report_path(run_directory, "")
    if report_path is None:
        _error(
            f"No report artifact exists for run {run_id!r}.",
            json_output=False,
        )
        raise typer.Exit(code=1)
    try:
        content = report_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        _error(f"Could not read report for run {run_id!r}: {exc}", json_output=False)
        raise typer.Exit(code=1) from exc
    typer.echo(content, nl=not content.endswith("\n"))


@app.command()
def cleanup(
    run_id: Annotated[
        str | None, typer.Option("--run-id", help="Limit cleanup to one owned run.")
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit one machine-readable JSON result."),
    ] = False,
) -> None:
    """Remove only ReproScout-owned Docker containers."""

    if run_id is not None:
        _validate_run_id(run_id)
    try:
        docker = resolve_docker_command()
        filters = ["--filter", "label=reproscout.managed=true"]
        if run_id is not None:
            filters.extend(["--filter", f"label=reproscout.run_id={run_id}"])
        listed = subprocess.run(
            [*docker, "ps", "-aq", *filters],
            capture_output=True,
            check=False,
            text=True,
            timeout=15,
        )
        if listed.returncode != 0:
            raise RuntimeError("Docker did not return the owned-container list.")
        ids = [value.strip() for value in listed.stdout.splitlines() if value.strip()]
        if any(not _SAFE_CONTAINER_ID.fullmatch(value) for value in ids):
            raise RuntimeError("Docker returned an invalid owned-container identifier.")
        removed: list[str] = []
        for container_id in ids:
            result = subprocess.run(
                [*docker, "rm", "--force", container_id],
                capture_output=True,
                check=False,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                raise RuntimeError("Docker could not remove an owned container.")
            removed.append(container_id)
    except (OSError, RuntimeError) as exc:
        _error(redact_sensitive_text(str(exc)), json_output=json_output)
        raise typer.Exit(code=1) from exc
    payload = {"removed": removed}
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        typer.echo(f"Removed {len(removed)} ReproScout-owned container(s).")


@app.command()
def config(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit one machine-readable JSON result."),
    ] = False,
) -> None:
    """Show secret-free local configuration and capability status."""

    doctor(json_output=json_output)


@app.command()
def serve(
    host: Annotated[
        str,
        typer.Option("--host", help="Bind address; loopback is the safe default."),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option("--port", min=1, max=65_535, help="Local API port."),
    ] = 8000,
    runs_dir: Annotated[
        Path | None,
        typer.Option(
            "--runs-dir", help="Directory containing run state.", path_type=Path
        ),
    ] = None,
) -> None:
    """Serve the optional local FastAPI control API."""

    try:
        from .api import run_server
    except ImportError as exc:
        _error(
            "The local API is optional; install reproscout[api] first.",
            json_output=False,
        )
        raise typer.Exit(code=1) from exc
    run_server(host=host, port=port, runs_dir=runs_dir)


def _resolve_run_directory(run_id: str, runs_dir: Path | None) -> Path:
    _validate_run_id(run_id)
    root = (
        (runs_dir if runs_dir is not None else get_settings().runs_dir)
        .expanduser()
        .resolve()
    )
    candidate = (root / run_id).resolve()
    if (root / run_id).is_symlink():
        _error("Run directory cannot be a symlink.", json_output=False)
        raise typer.Exit(code=1)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        _error("Run ID escapes the configured runs directory.", json_output=False)
        raise typer.Exit(code=1) from exc
    if not candidate.is_dir():
        _error(f"Run directory not found for {run_id!r}.", json_output=False)
        raise typer.Exit(code=1)
    return candidate


def _display_report_path(root: Path, run_id: str) -> Path | None:
    """Return the ordinary report or the environment-preflight report."""

    directory = root if not run_id else root / run_id
    for filename in ("report.md", "environment-report.md"):
        candidate = directory / filename
        if candidate.is_file():
            return candidate
    return None


def _validate_run_id(run_id: str) -> None:
    if not _SAFE_RUN_ID.fullmatch(run_id):
        _error("Run ID contains unsupported path characters.", json_output=False)
        raise typer.Exit(code=1)


def _error(message: str, *, json_output: bool) -> None:
    safe = redact_sensitive_text(message)
    if json_output:
        typer.echo(json.dumps({"error": safe}, ensure_ascii=False), err=True)
    else:
        typer.echo(safe, err=True)


if __name__ == "__main__":
    app()
