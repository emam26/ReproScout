"""Repository intake primitives."""

from .clone import (
    CloneError,
    CloneResult,
    RunWorkspace,
    RunWorkspaceError,
    clone_repository,
    clone_repository_at_commit,
    create_run_workspace,
)
from .manifest import (
    ManifestError,
    ManifestScanLimits,
    RepositoryManifest,
    build_manifest,
    is_documentation_path,
)
from .urls import GitHubRepository, RepositoryUrlError, parse_github_url

__all__ = [
    "CloneError",
    "CloneResult",
    "GitHubRepository",
    "ManifestError",
    "ManifestScanLimits",
    "RepositoryManifest",
    "RepositoryUrlError",
    "RunWorkspace",
    "RunWorkspaceError",
    "build_manifest",
    "clone_repository",
    "clone_repository_at_commit",
    "create_run_workspace",
    "is_documentation_path",
    "parse_github_url",
]
