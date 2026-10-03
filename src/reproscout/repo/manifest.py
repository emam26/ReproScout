"""Deterministic structural inspection of a cloned repository."""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path

from pydantic import BaseModel, Field

from .clone import CloneResult
from .urls import GitHubRepository


class ManifestError(RuntimeError):
    """Raised when a repository cannot be structurally inspected."""


class RepositoryManifest(BaseModel):
    """Typed deterministic metadata for an inspected repository."""

    repository_url: str
    normalized_url: str
    owner: str
    repository_name: str
    commit_sha: str
    branch: str | None = None
    workspace_path: Path
    important_files: list[str] = Field(default_factory=list)
    documentation_files: list[str] = Field(default_factory=list)
    dependency_files: list[str] = Field(default_factory=list)
    test_indicators: list[str] = Field(default_factory=list)
    dockerfile_indicators: list[str] = Field(default_factory=list)
    ci_workflow_indicators: list[str] = Field(default_factory=list)
    example_indicators: list[str] = Field(default_factory=list)
    script_indicators: list[str] = Field(default_factory=list)
    config_indicators: list[str] = Field(default_factory=list)
    has_tests: bool = False
    has_dockerfile: bool = False
    has_ci_workflows: bool = False


_DOCUMENTATION_PREFIXES = ("readme", "install", "contributing")
_DEPENDENCY_NAMES = {
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "environment.yml",
    "environment.yaml",
}
_TEST_DIRECTORIES = {"test", "tests"}
_EXAMPLE_DIRECTORIES = {"example", "examples", "demo"}
_SCRIPT_DIRECTORIES = {"scripts"}
_CONFIG_DIRECTORIES = {"config", "configs"}
_SUPPORT_FILES = {"makefile", "tox.ini", "pytest.ini"}


def _relative_path(root: Path, path: Path, *, directory: bool = False) -> str:
    relative = path.relative_to(root).as_posix()
    return f"{relative}/" if directory else relative


def _is_under_directory(parts: tuple[str, ...], names: set[str]) -> bool:
    return any(part.lower() in names for part in parts[:-1])


def _is_workflow_path(parts: tuple[str, ...]) -> bool:
    lowered = tuple(part.lower() for part in parts)
    return any(
        lowered[index : index + 2] == (".github", "workflows")
        for index in range(len(lowered) - 1)
    )


def _inspect_paths(root: Path) -> list[tuple[str, bool]]:
    entries: list[tuple[str, bool]] = []
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        directories[:] = sorted(
            directory for directory in directories if directory.lower() != ".git"
        )
        for directory in directories:
            directory_path = current_path / directory
            relative = _relative_path(root, directory_path, directory=True)
            entries.append((relative, True))
        for filename in sorted(files):
            file_path = current_path / filename
            relative = _relative_path(root, file_path)
            if ".git" not in {part.lower() for part in Path(relative).parts}:
                entries.append((relative, False))
    return sorted(entries, key=lambda entry: (entry[0].lower(), entry[0]))


def build_manifest(
    repository: GitHubRepository,
    clone: CloneResult,
) -> RepositoryManifest:
    """Inspect a cloned repository without reading file contents."""

    root = clone.workspace_path
    if not root.is_dir():
        raise ManifestError(f"Repository workspace does not exist: {root}")

    documentation_files: set[str] = set()
    dependency_files: set[str] = set()
    test_indicators: set[str] = set()
    dockerfile_indicators: set[str] = set()
    ci_workflow_indicators: set[str] = set()
    example_indicators: set[str] = set()
    script_indicators: set[str] = set()
    config_indicators: set[str] = set()
    important_files: set[str] = set()

    for relative, is_directory in _inspect_paths(root):
        path = Path(relative.rstrip("/"))
        parts = tuple(path.parts)
        basename = path.name.lower()
        if is_directory:
            if basename in _TEST_DIRECTORIES:
                test_indicators.add(relative)
            if basename in _EXAMPLE_DIRECTORIES:
                example_indicators.add(relative)
            if basename in _SCRIPT_DIRECTORIES:
                script_indicators.add(relative)
            if basename in _CONFIG_DIRECTORIES:
                config_indicators.add(relative)
            if tuple(part.lower() for part in parts[-2:]) == (".github", "workflows"):
                ci_workflow_indicators.add(relative)
            continue

        if basename.startswith(_DOCUMENTATION_PREFIXES):
            documentation_files.add(relative)
            important_files.add(relative)
        if (
            fnmatch.fnmatch(basename, "requirements*.txt")
            or basename in _DEPENDENCY_NAMES
        ):
            dependency_files.add(relative)
            important_files.add(relative)
        if basename.startswith("dockerfile"):
            dockerfile_indicators.add(relative)
            important_files.add(relative)
        if _is_workflow_path(parts):
            ci_workflow_indicators.add(relative)
            important_files.add(relative)
        if (
            _is_under_directory(parts, _TEST_DIRECTORIES)
            or basename in {"test", "test.py"}
            or basename.startswith("test_")
        ):
            test_indicators.add(relative)
            important_files.add(relative)
        if _is_under_directory(parts, _EXAMPLE_DIRECTORIES):
            example_indicators.add(relative)
            important_files.add(relative)
        if _is_under_directory(parts, _SCRIPT_DIRECTORIES):
            script_indicators.add(relative)
            important_files.add(relative)
        if _is_under_directory(parts, _CONFIG_DIRECTORIES):
            config_indicators.add(relative)
            important_files.add(relative)
        if basename in _SUPPORT_FILES:
            important_files.add(relative)

    return RepositoryManifest(
        repository_url=repository.original_url,
        normalized_url=repository.normalized_url,
        owner=repository.owner,
        repository_name=repository.name,
        commit_sha=clone.commit_sha,
        branch=clone.branch,
        workspace_path=root,
        important_files=sorted(important_files),
        documentation_files=sorted(documentation_files),
        dependency_files=sorted(dependency_files),
        test_indicators=sorted(test_indicators),
        dockerfile_indicators=sorted(dockerfile_indicators),
        ci_workflow_indicators=sorted(ci_workflow_indicators),
        example_indicators=sorted(example_indicators),
        script_indicators=sorted(script_indicators),
        config_indicators=sorted(config_indicators),
        has_tests=bool(test_indicators),
        has_dockerfile=bool(dockerfile_indicators),
        has_ci_workflows=bool(ci_workflow_indicators),
    )
