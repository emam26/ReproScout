from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from reproscout.repo.clone import (
    CloneError,
    CloneResult,
    clone_repository,
    create_run_workspace,
)
from reproscout.repo.manifest import ManifestScanLimits, build_manifest
from reproscout.repo.urls import RepositoryUrlError, parse_github_url


def _run_git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _create_fixture_repository(
    root: Path,
    files: dict[str, str],
) -> tuple[Path, str]:
    repository = root / "source"
    repository.mkdir()
    for relative_path, contents in files.items():
        path = repository / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")

    _run_git(["init"], repository)
    _run_git(["config", "user.name", "ReproScout Tests"], repository)
    _run_git(["config", "user.email", "tests@example.invalid"], repository)
    _run_git(["add", "."], repository)
    _run_git(["commit", "-m", "fixture"], repository)
    _run_git(["branch", "-M", "main"], repository)
    return repository, _run_git(["rev-parse", "HEAD"], repository)


def test_valid_github_urls_are_normalized() -> None:
    repository = parse_github_url("https://github.com/Owner/Repository.git")

    assert repository.owner == "Owner"
    assert repository.name == "Repository"
    assert repository.full_name == "Owner/Repository"
    assert repository.normalized_url == "https://github.com/Owner/Repository"


@pytest.mark.parametrize(
    "value",
    [
        "http://github.com/owner/repository",
        "https://gitlab.com/owner/repository",
        "https://github.com/owner",
        "https://github.com/owner/repository/issues/1",
        "https://github.com/owner/repository/pull/1",
        "https://github.com/owner/repository/blob/main/README.md",
        "git@github.com:owner/repository.git",
        "https://github.com//repository",
    ],
)
def test_invalid_or_unsupported_urls_are_rejected(value: str) -> None:
    with pytest.raises(RepositoryUrlError):
        parse_github_url(value)


def test_fixture_a_manifest_captures_files_and_excludes_git(tmp_path: Path) -> None:
    source, commit_sha = _create_fixture_repository(
        tmp_path,
        {
            "README.md": "fixture A",
            "requirements.txt": "pytest",
            "nested/requirements-dev.txt": "ruff",
            "src/example.py": "value = 1",
            "tests/test_example.py": "def test_example(): pass",
        },
    )
    workspace = create_run_workspace(tmp_path / "runs")
    clone = clone_repository(source.as_uri(), workspace.repository_path)
    manifest = build_manifest(
        parse_github_url("https://github.com/example/fixture-a"),
        clone,
    )

    assert clone.commit_sha == commit_sha
    assert clone.branch == "main"
    assert manifest.commit_sha == commit_sha
    assert manifest.branch == "main"
    assert manifest.documentation_files == ["README.md"]
    assert manifest.dependency_files == [
        "nested/requirements-dev.txt",
        "requirements.txt",
    ]
    assert manifest.has_tests is True
    assert "tests/" in manifest.test_indicators
    assert "tests/test_example.py" in manifest.test_indicators
    assert all(".git" not in path.split("/") for path in manifest.important_files)
    assert manifest.important_files == sorted(manifest.important_files)


def test_fixture_b_manifest_captures_structural_indicators(tmp_path: Path) -> None:
    source, _ = _create_fixture_repository(
        tmp_path,
        {
            "pyproject.toml": "[project]\nname = 'fixture-b'\n",
            "Dockerfile": "FROM python:3.11\n",
            ".github/workflows/test.yml": "name: test\n",
            "examples/demo.py": "print('demo')\n",
            "configs/default.yml": "enabled: true\n",
            "scripts/check.py": "print('check')\n",
        },
    )
    workspace = create_run_workspace(tmp_path / "runs")
    clone = clone_repository(source.as_uri(), workspace.repository_path)
    manifest = build_manifest(
        parse_github_url("https://github.com/example/fixture-b"),
        clone,
    )

    assert manifest.dependency_files == ["pyproject.toml"]
    assert manifest.dockerfile_indicators == ["Dockerfile"]
    assert manifest.ci_workflow_indicators == [
        ".github/workflows/",
        ".github/workflows/test.yml",
    ]
    assert manifest.example_indicators == ["examples/", "examples/demo.py"]
    assert manifest.config_indicators == ["configs/", "configs/default.yml"]
    assert manifest.script_indicators == ["scripts/", "scripts/check.py"]
    assert manifest.has_dockerfile is True
    assert manifest.has_ci_workflows is True


def test_manifest_recognizes_nested_setup_documentation(tmp_path: Path) -> None:
    source, _ = _create_fixture_repository(
        tmp_path,
        {
            "README.md": "fixture documentation\n",
            "docs/setup.md": "setup notes\n",
            "docs/guides/pipeline.md": "pipeline notes\n",
            "environments/setup-notes.rst": "environment setup\n",
            "docs/architecture.md": "not a setup source\n",
        },
    )
    workspace = create_run_workspace(tmp_path / "runs")
    clone = clone_repository(source.as_uri(), workspace.repository_path)

    manifest = build_manifest(
        parse_github_url("https://github.com/example/nested-documentation"),
        clone,
    )

    assert manifest.documentation_files == [
        "README.md",
        "docs/guides/pipeline.md",
        "docs/setup.md",
        "environments/setup-notes.rst",
    ]
    assert manifest.scan_truncated is False


def test_manifest_records_bounded_discovery_as_incomplete(tmp_path: Path) -> None:
    source, _ = _create_fixture_repository(
        tmp_path,
        {
            "README.md": "fixture documentation\n",
            "docs/guides/pipeline.md": "pipeline notes\n",
            "docs/setup.md": "setup notes\n",
        },
    )
    workspace = create_run_workspace(tmp_path / "runs")
    clone = clone_repository(source.as_uri(), workspace.repository_path)

    manifest = build_manifest(
        parse_github_url("https://github.com/example/bounded-documentation"),
        clone,
        limits=ManifestScanLimits(
            max_depth=1,
            max_entries=100,
            max_documentation_files=1,
            max_dependency_files=50,
        ),
    )

    assert manifest.scan_truncated is True
    assert any("depth limit" in warning for warning in manifest.scan_warnings)
    assert any("file limit" in warning for warning in manifest.scan_warnings)
    assert len(manifest.documentation_files) == 1


def test_manifest_skips_symlinked_setup_documentation(tmp_path: Path) -> None:
    source, _ = _create_fixture_repository(
        tmp_path,
        {
            "README.md": "fixture documentation\n",
            "notes.md": "setup notes\n",
        },
    )
    docs = source / "docs"
    docs.mkdir()
    try:
        (docs / "setup.md").symlink_to("../notes.md")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable in this environment")
    _run_git(["add", "docs/setup.md"], source)
    _run_git(["commit", "-m", "add setup symlink"], source)

    workspace = create_run_workspace(tmp_path / "runs")
    clone = clone_repository(source.as_uri(), workspace.repository_path)
    manifest = build_manifest(
        parse_github_url("https://github.com/example/symlinked-documentation"),
        clone,
    )

    assert manifest.documentation_files == ["README.md"]
    assert manifest.scan_truncated is True
    assert "symlinked setup source" in manifest.scan_warnings[0]


def test_manifest_ignores_irrelevant_symlink_without_partial_result(
    tmp_path: Path,
) -> None:
    source, _ = _create_fixture_repository(
        tmp_path,
        {
            "README.md": "fixture documentation\n",
            "src/main.py": "print('fixture')\n",
        },
    )
    try:
        (source / "src" / "helper.py").symlink_to("main.py")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable in this environment")
    _run_git(["add", "src/helper.py"], source)
    _run_git(["commit", "-m", "add irrelevant symlink"], source)

    workspace = create_run_workspace(tmp_path / "runs")
    clone = clone_repository(source.as_uri(), workspace.repository_path)
    manifest = build_manifest(
        parse_github_url("https://github.com/example/irrelevant-symlink"),
        clone,
    )

    assert manifest.documentation_files == ["README.md"]
    assert manifest.scan_truncated is False
    assert manifest.scan_warnings == []


def test_manifest_ignores_irrelevant_binary_file(tmp_path: Path) -> None:
    source, _ = _create_fixture_repository(
        tmp_path,
        {"README.md": "fixture documentation\n"},
    )
    binary_path = source / "assets" / "weights.bin"
    binary_path.parent.mkdir()
    binary_path.write_bytes(b"\x00\x01\x02")
    _run_git(["add", "assets/weights.bin"], source)
    _run_git(["commit", "-m", "add irrelevant binary"], source)

    workspace = create_run_workspace(tmp_path / "runs")
    clone = clone_repository(source.as_uri(), workspace.repository_path)
    manifest = build_manifest(
        parse_github_url("https://github.com/example/irrelevant-binary"),
        clone,
    )

    assert manifest.scan_truncated is False
    assert manifest.scan_warnings == []


def test_run_workspace_paths_are_isolated_and_collision_resistant(
    tmp_path: Path,
) -> None:
    first = create_run_workspace(tmp_path / "runs")
    second = create_run_workspace(tmp_path / "runs")

    assert first.run_id != second.run_id
    assert first.repository_path == first.run_dir / "workspace" / "repository"
    assert first.workspace_dir.is_dir()
    assert second.workspace_dir.is_dir()
    assert not first.repository_path.exists()


def test_clone_failure_is_reported(tmp_path: Path) -> None:
    destination = tmp_path / "runs" / "run" / "workspace" / "repository"

    with pytest.raises(CloneError, match="Git command failed"):
        clone_repository((tmp_path / "does-not-exist").as_uri(), destination)


def test_clone_result_requires_a_full_pinned_revision(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        CloneResult(
            repository_url="https://github.com/example/project",
            workspace_path=tmp_path,
            commit_sha="short-revision",
        )
