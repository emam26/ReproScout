"""Controlled Git cloning and per-run workspace creation."""

from __future__ import annotations

import os
import re
import subprocess
import uuid
from pathlib import Path

from pydantic import BaseModel


class CloneError(RuntimeError):
    """Raised when a repository cannot be cloned or inspected with Git."""


class RunWorkspaceError(RuntimeError):
    """Raised when an isolated run workspace cannot be created."""


class RunWorkspace(BaseModel):
    """Paths allocated for one repository intake run."""

    run_id: str
    run_dir: Path
    workspace_dir: Path
    repository_path: Path


class CloneResult(BaseModel):
    """Deterministic metadata captured from a successful Git clone."""

    repository_url: str
    workspace_path: Path
    commit_sha: str
    branch: str | None = None


_CREDENTIAL_URL_PATTERN = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)[^/\s@]+@")


def create_run_workspace(runs_dir: Path) -> RunWorkspace:
    """Create a collision-resistant isolated workspace for one run."""

    runs_root = Path(runs_dir).expanduser().resolve()
    try:
        runs_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RunWorkspaceError(
            f"Could not create runs directory: {runs_root}"
        ) from exc

    for _ in range(5):
        run_id = uuid.uuid4().hex
        run_dir = runs_root / run_id
        try:
            run_dir.mkdir()
        except FileExistsError:
            continue
        except OSError as exc:
            raise RunWorkspaceError(
                f"Could not create run directory: {run_dir}"
            ) from exc

        workspace_dir = run_dir / "workspace"
        repository_path = workspace_dir / "repository"
        try:
            workspace_dir.mkdir()
        except OSError as exc:
            raise RunWorkspaceError(
                f"Could not create workspace directory: {workspace_dir}"
            ) from exc

        return RunWorkspace(
            run_id=run_id,
            run_dir=run_dir,
            workspace_dir=workspace_dir,
            repository_path=repository_path,
        )

    raise RunWorkspaceError("Could not allocate a unique run directory.")


def _clean_git_output(output: str) -> str:
    """Return concise Git output without credential-like URL components."""

    message = output.strip().replace("\x00", "")
    if not message:
        return "no additional Git output"
    message = _CREDENTIAL_URL_PATTERN.sub(r"\g<scheme><redacted>@", message)
    return message[-500:]


def _run_git(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout_seconds: int,
    allow_failure: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run Git without a shell and with a bounded timeout."""

    try:
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": os.devnull,
        }
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            check=False,
            env=environment,
            text=True,
            timeout=timeout_seconds,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise CloneError("Git is not available on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise CloneError(
            f"Git command timed out after {timeout_seconds} seconds."
        ) from exc

    if completed.returncode != 0 and not allow_failure:
        output = completed.stderr or completed.stdout
        raise CloneError(
            f"Git command failed with exit code {completed.returncode}: "
            f"{_clean_git_output(output)}"
        )
    return completed


def clone_repository(
    repository_url: str,
    destination: Path,
    *,
    timeout_seconds: int = 120,
) -> CloneResult:
    """Clone a repository and capture its checked-out revision metadata.

    The cloned repository is only inspected after cloning. No repository code,
    hooks, dependencies, or scripts are executed by this function.
    """

    destination_input = Path(destination).expanduser()
    if destination_input.is_symlink():
        raise CloneError("Git clone destination cannot be a symlink.")
    destination = destination_input.resolve()
    if destination.exists():
        raise CloneError("Git clone destination must be a new real directory path.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run_git(
        [
            "-c",
            "credential.helper=",
            "-c",
            "core.hooksPath=NUL" if os.name == "nt" else "core.hooksPath=/dev/null",
            "clone",
            "--no-recurse-submodules",
            "--quiet",
            "--",
            repository_url,
            str(destination),
        ],
        timeout_seconds=timeout_seconds,
    )

    commit_sha = _run_git(
        ["rev-parse", "HEAD"],
        cwd=destination,
        timeout_seconds=timeout_seconds,
    ).stdout.strip()
    branch_result = _run_git(
        ["symbolic-ref", "--quiet", "--short", "HEAD"],
        cwd=destination,
        timeout_seconds=timeout_seconds,
        allow_failure=True,
    )
    if branch_result.returncode == 0:
        branch = branch_result.stdout.strip() or None
    elif branch_result.returncode == 1:
        branch = None
    else:
        output = branch_result.stderr or branch_result.stdout
        raise CloneError(
            f"Could not determine the checked-out branch: {_clean_git_output(output)}"
        )

    return CloneResult(
        repository_url=repository_url,
        workspace_path=destination,
        commit_sha=commit_sha,
        branch=branch,
    )
