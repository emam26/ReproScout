"""Validation and normalization for supported repository URLs."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from pydantic import BaseModel


class RepositoryUrlError(ValueError):
    """Raised when a repository URL is unsupported or malformed."""


class GitHubRepository(BaseModel):
    """A normalized public GitHub repository reference."""

    owner: str
    name: str
    original_url: str
    normalized_url: str

    @property
    def full_name(self) -> str:
        """Return the owner/name repository identifier."""

        return f"{self.owner}/{self.name}"


_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def parse_github_url(value: str) -> GitHubRepository:
    """Validate and normalize a supported HTTPS GitHub repository URL."""

    if not isinstance(value, str) or not value.strip():
        raise RepositoryUrlError(
            "Repository URL must be an HTTPS GitHub URL in the form "
            "https://github.com/owner/repository."
        )

    original_url = value.strip()
    try:
        parsed = urlsplit(original_url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise RepositoryUrlError("Repository URL is malformed.") from exc

    if (
        parsed.scheme.lower() != "https"
        or hostname is None
        or hostname.lower() != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise RepositoryUrlError(
            "Only HTTPS GitHub repository URLs are supported: "
            "https://github.com/owner/repository."
        )

    path_parts = parsed.path.strip("/").split("/")
    if len(path_parts) != 2 or not all(path_parts):
        raise RepositoryUrlError(
            "GitHub URL must identify exactly one repository: "
            "https://github.com/owner/repository."
        )

    owner, name = path_parts
    if name.lower().endswith(".git"):
        name = name[:-4]

    if (
        not name
        or not _NAME_PATTERN.fullmatch(owner)
        or not _NAME_PATTERN.fullmatch(name)
    ):
        raise RepositoryUrlError("GitHub owner and repository names are malformed.")

    return GitHubRepository(
        owner=owner,
        name=name,
        original_url=original_url,
        normalized_url=f"https://github.com/{owner}/{name}",
    )
