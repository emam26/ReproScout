"""Repository intake primitives."""

from .clone import (
    CloneError,
    CloneResult,
    RunWorkspace,
    RunWorkspaceError,
    clone_repository,
    create_run_workspace,
)
from .manifest import ManifestError, RepositoryManifest, build_manifest
from .urls import GitHubRepository, RepositoryUrlError, parse_github_url

__all__ = [
    "CloneError",
    "CloneResult",
    "GitHubRepository",
    "ManifestError",
    "RepositoryManifest",
    "RepositoryUrlError",
    "RunWorkspace",
    "RunWorkspaceError",
    "build_manifest",
    "clone_repository",
    "create_run_workspace",
    "parse_github_url",
]
