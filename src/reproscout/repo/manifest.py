"""Deterministic structural inspection of a cloned repository."""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from .clone import CloneResult
from .urls import GitHubRepository


class ManifestError(RuntimeError):
    """Raised when a repository cannot be structurally inspected."""


@dataclass(frozen=True, slots=True)
class ManifestScanLimits:
    """Bounds for deterministic repository structure discovery."""

    max_depth: int = 12
    max_entries: int = 20_000
    max_documentation_files: int = 100
    max_dependency_files: int = 50

    def __post_init__(self) -> None:
        if (
            self.max_depth < 1
            or self.max_entries < 1
            or self.max_documentation_files < 1
            or self.max_dependency_files < 1
        ):
            raise ValueError("Manifest scan limits must be positive.")


class RepositoryManifest(BaseModel):
    """Typed deterministic metadata for an inspected repository."""

    repository_url: str
    normalized_url: str
    owner: str
    repository_name: str
    commit_sha: str = Field(pattern=r"^[0-9a-fA-F]{40}$")
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
    scan_truncated: bool = False
    scan_warnings: list[str] = Field(default_factory=list, max_length=100)


_DOCUMENTATION_PREFIXES = ("readme", "install", "contributing")
_DOCUMENTATION_STEM_PREFIXES = (
    "setup",
    "environment",
    "pipeline",
    "quickstart",
    "getting-started",
    "usage",
)
_DOCUMENTATION_EXTENSIONS = {"", ".md", ".mdx", ".rst", ".txt"}
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


def is_documentation_path(relative: str | Path) -> bool:
    """Return whether a repository-relative path is common setup documentation."""

    path = Path(relative)
    name = path.name.lower()
    stem = path.stem.lower()
    if path.suffix.lower() not in _DOCUMENTATION_EXTENSIONS:
        return False
    return name.startswith(_DOCUMENTATION_PREFIXES) or stem.startswith(
        _DOCUMENTATION_STEM_PREFIXES
    )


def _is_preflight_source_path(relative: str) -> bool:
    """Return whether a file path can contribute setup evidence."""

    path = Path(relative)
    basename = path.name.lower()
    return (
        is_documentation_path(relative)
        or fnmatch.fnmatch(basename, "requirements*.txt")
        or basename in _DEPENDENCY_NAMES
        or basename.startswith("dockerfile")
        or _is_workflow_path(tuple(path.parts))
    )


def _inspect_paths(
    root: Path,
    limits: ManifestScanLimits,
) -> tuple[list[tuple[str, bool]], bool, list[str]]:
    if root.is_symlink():
        raise ManifestError("Repository workspace cannot be a symlink.")

    entries: list[tuple[str, bool]] = []
    scan_truncated = False
    warnings: list[str] = []

    def mark_incomplete(message: str) -> None:
        nonlocal scan_truncated
        scan_truncated = True
        if message not in warnings:
            warnings.append(message)

    def add_entry(relative: str, is_directory: bool) -> bool:
        if len(entries) >= limits.max_entries:
            mark_incomplete(
                "Repository structure scan reached the entry limit; some paths were "
                "not inspected."
            )
            return False
        entries.append((relative, is_directory))
        return True

    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        relative_current = current_path.relative_to(root)
        current_depth = (
            0 if relative_current == Path(".") else len(relative_current.parts)
        )
        discovered_directories = sorted(
            directories, key=lambda value: (value.lower(), value)
        )
        directories[:] = []
        for directory in discovered_directories:
            if directory.lower() == ".git":
                continue
            directory_path = current_path / directory
            if directory_path.is_symlink():
                mark_incomplete(
                    "Repository structure scan skipped a symlinked directory."
                )
                continue
            if current_depth + 1 > limits.max_depth:
                mark_incomplete(
                    "Repository structure scan reached the directory-depth limit; "
                    "some paths were not inspected."
                )
                continue
            relative = _relative_path(root, directory_path, directory=True)
            if not add_entry(relative, True):
                return (
                    sorted(entries, key=lambda entry: (entry[0].lower(), entry[0])),
                    scan_truncated,
                    warnings,
                )
            directories.append(directory)
        for filename in sorted(files):
            file_path = current_path / filename
            relative = _relative_path(root, file_path)
            if file_path.is_symlink():
                if _is_preflight_source_path(relative):
                    mark_incomplete(
                        "Repository structure scan skipped a symlinked setup source."
                    )
                continue
            if ".git" not in {
                part.lower() for part in Path(relative).parts
            } and not add_entry(relative, False):
                return (
                    sorted(entries, key=lambda entry: (entry[0].lower(), entry[0])),
                    scan_truncated,
                    warnings,
                )
    return (
        sorted(entries, key=lambda entry: (entry[0].lower(), entry[0])),
        scan_truncated,
        warnings,
    )


def build_manifest(
    repository: GitHubRepository,
    clone: CloneResult,
    *,
    limits: ManifestScanLimits | None = None,
) -> RepositoryManifest:
    """Inspect a cloned repository without reading file contents."""

    root = clone.workspace_path
    if not root.is_dir():
        raise ManifestError(f"Repository workspace does not exist: {root}")

    policy = limits or ManifestScanLimits()
    documentation_files: set[str] = set()
    dependency_files: set[str] = set()
    test_indicators: set[str] = set()
    dockerfile_indicators: set[str] = set()
    ci_workflow_indicators: set[str] = set()
    example_indicators: set[str] = set()
    script_indicators: set[str] = set()
    config_indicators: set[str] = set()
    important_files: set[str] = set()

    entries, scan_truncated, scan_warnings = _inspect_paths(root, policy)
    for relative, is_directory in entries:
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

        if is_documentation_path(relative):
            if len(documentation_files) < policy.max_documentation_files:
                documentation_files.add(relative)
                important_files.add(relative)
            else:
                scan_truncated = True
                warning = (
                    "Repository documentation discovery reached the file limit; "
                    "some setup documentation was not inspected."
                )
                if warning not in scan_warnings:
                    scan_warnings.append(warning)
        if (
            fnmatch.fnmatch(basename, "requirements*.txt")
            or basename in _DEPENDENCY_NAMES
        ):
            if len(dependency_files) < policy.max_dependency_files:
                dependency_files.add(relative)
                important_files.add(relative)
            else:
                scan_truncated = True
                warning = (
                    "Repository dependency discovery reached the file limit; "
                    "some dependency declarations were not inspected."
                )
                if warning not in scan_warnings:
                    scan_warnings.append(warning)
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
        scan_truncated=scan_truncated,
        scan_warnings=scan_warnings,
    )
