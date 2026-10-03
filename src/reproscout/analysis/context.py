"""Bounded, repository-local context extraction for optional LLM interpretation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from reproscout.repo import RepositoryManifest
from reproscout.security import BoundedReadError, read_bounded_workspace_file


@dataclass(frozen=True, slots=True)
class ContextLimits:
    """Hard limits that prevent arbitrary repository dumps."""

    max_files: int = 20
    max_file_bytes: int = 64 * 1024
    max_total_characters: int = 120_000

    def __post_init__(self) -> None:
        if (
            self.max_files < 1
            or self.max_file_bytes < 1
            or self.max_total_characters < 1
        ):
            raise ValueError("Context limits must be positive.")


@dataclass(frozen=True, slots=True)
class ContextDocument:
    """One bounded UTF-8 repository document."""

    path: str
    text: str


_RELEVANT_NAMES = {
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "environment.yml",
    "environment.yaml",
    "makefile",
    "tox.ini",
    "pytest.ini",
}
_SECRET_ASSIGNMENT = re.compile(
    r"(?im)^(?P<prefix>\s*(?:export\s+)?(?:api_?key|access_?token|password|"
    r"client_?secret|authorization)\s*[=:]\s*)(?P<value>[^\s#]+)"
)


def _is_relevant(path: str) -> bool:
    parts = Path(path).parts
    name = Path(path).name.lower()
    if name in _RELEVANT_NAMES:
        return True
    if name.startswith(("readme", "install", "requirements", "dockerfile")):
        return True
    lowered = tuple(part.lower() for part in parts)
    return len(lowered) >= 3 and lowered[-3:-1] == (".github", "workflows")


def collect_context_documents(
    manifest: RepositoryManifest,
    limits: ContextLimits | None = None,
) -> list[ContextDocument]:
    """Read only bounded relevant text files beneath the manifest workspace."""

    policy = limits or ContextLimits()
    root = manifest.workspace_path.resolve()
    candidates = sorted(
        {
            *manifest.important_files,
            *manifest.documentation_files,
            *manifest.dependency_files,
            *manifest.ci_workflow_indicators,
            *manifest.dockerfile_indicators,
        },
        key=lambda value: (value.lower(), value),
    )
    documents: list[ContextDocument] = []
    total_characters = 0
    for relative in candidates:
        if len(documents) >= policy.max_files or not _is_relevant(relative):
            continue
        relative_path = Path(relative)
        lowered_parts = {part.lower() for part in relative_path.parts}
        if (
            relative_path.is_absolute()
            or ".." in relative_path.parts
            or ".git" in lowered_parts
        ):
            continue
        try:
            raw = read_bounded_workspace_file(
                root,
                relative_path.as_posix(),
                max_bytes=policy.max_file_bytes,
            )
        except BoundedReadError:
            continue
        if b"\x00" in raw:
            continue
        text = raw.decode("utf-8", errors="replace")
        text = _SECRET_ASSIGNMENT.sub(r"\g<prefix><redacted>", text)
        remaining = policy.max_total_characters - total_characters
        if remaining <= 0:
            break
        text = text[:remaining]
        documents.append(ContextDocument(path=relative_path.as_posix(), text=text))
        total_characters += len(text)
    return documents
