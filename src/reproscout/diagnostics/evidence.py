"""Deterministic bounded evidence selection and repeated-failure signatures."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from reproscout.security import redact_sensitive_text

from .models import (
    DiagnosticContext,
    DiagnosticEvidenceItem,
    EnvironmentFingerprint,
    EvidenceKind,
    NormalizedFailure,
)

_WHITESPACE = re.compile(r"\s+")
_HEX_IDENTIFIER = re.compile(r"\b[0-9a-f]{12,}\b", re.IGNORECASE)
_ABSOLUTE_PATH = re.compile(r"(?:[A-Za-z]:\\|/)(?:[^\s:]+[/\\])+[^\s:]+")
_LINE_NUMBER = re.compile(r"(?<=line )\d+|:\d+(?::\d+)?")


@dataclass(frozen=True, slots=True)
class EvidenceLimits:
    max_characters: int = 16_000
    max_item_characters: int = 2_000
    max_items: int = 30

    def __post_init__(self) -> None:
        if (
            self.max_characters < 500
            or self.max_item_characters < 100
            or self.max_items < 1
        ):
            raise ValueError("Evidence limits are too small.")


def normalize_error_signature(value: str) -> str:
    """Remove volatile details while preserving the substantive error identity."""

    normalized = redact_sensitive_text(value).lower()
    normalized = _ABSOLUTE_PATH.sub("<path>", normalized)
    normalized = _HEX_IDENTIFIER.sub("<id>", normalized)
    normalized = _LINE_NUMBER.sub(":<line>", normalized)
    return _WHITESPACE.sub(" ", normalized).strip()[:4_000]


def failure_signature(
    failure: NormalizedFailure,
    *,
    command: str,
    environment: EnvironmentFingerprint | None = None,
) -> str:
    """Hash stable failure, command, and relevant environment identity."""

    payload = {
        "command": _WHITESPACE.sub(" ", command.strip()),
        "environment": (
            {
                "architecture": environment.architecture,
                "container_image": environment.container_image,
                "cuda_runtime": environment.cuda_runtime,
                "python_version": environment.python_version,
            }
            if environment is not None
            else None
        ),
        "failure_class": failure.failure_class.value,
        "normalized_error": normalize_error_signature(
            "\n".join(failure.important_lines) or failure.headline
        ),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _markdown_sections(text: str) -> list[str]:
    sections: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("#") and current:
            sections.append(current)
            current = []
        current.append(line)
    if current:
        sections.append(current)
    return ["\n".join(section).strip() for section in sections if any(section)]


def _relevance_terms(failure: NormalizedFailure) -> set[str]:
    terms = {
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_.-]{2,}", failure.headline)
    }
    terms.update(failure.failure_class.value.lower().split("_"))
    terms.difference_update({"error", "failed", "failure", "unknown", "with"})
    return terms


def _select_document_section(text: str, terms: set[str]) -> str | None:
    sections = _markdown_sections(text)
    if not sections:
        return None
    scored = [
        (sum(section.lower().count(term) for term in terms), index, section)
        for index, section in enumerate(sections)
    ]
    score, _, section = max(scored, key=lambda item: (item[0], -item[1]))
    return section if score > 0 else None


class EvidenceBuilder:
    """Reduce deterministic evidence before a later LLM diagnosis call."""

    def __init__(self, limits: EvidenceLimits | None = None) -> None:
        self.limits = limits or EvidenceLimits()

    def build(
        self,
        failure: NormalizedFailure,
        *,
        command: str,
        environment: EnvironmentFingerprint | None = None,
        dependency_evidence: list[str] | None = None,
        documentation: list[tuple[str, str]] | None = None,
        source_snippets: list[tuple[str, str]] | None = None,
        previous_attempts: list[str] | None = None,
        previous_repairs: list[str] | None = None,
    ) -> DiagnosticContext:
        terms = _relevance_terms(failure)
        candidates: list[tuple[EvidenceKind, str, str | None]] = [
            (
                EvidenceKind.FAILURE,
                "\n".join([failure.headline, *failure.important_lines]),
                failure.log_reference,
            )
        ]
        for value in dependency_evidence or []:
            candidates.append((EvidenceKind.DEPENDENCY, value, None))
        for path, text in documentation or []:
            section = _select_document_section(text, terms)
            if section:
                candidates.append((EvidenceKind.DOCUMENTATION, section, path))
        if environment is not None:
            environment_summary = json.dumps(
                {
                    "architecture": environment.architecture,
                    "container_image": environment.container_image,
                    "container_image_digest": environment.container_image_digest,
                    "cpu_allocation": environment.cpu_allocation,
                    "cuda_runtime": environment.cuda_runtime,
                    "gpu_visible": environment.gpu_visible,
                    "memory_limit": environment.memory_limit,
                    "network_mode": environment.network_mode,
                    "os": f"{environment.os_name} {environment.os_release}".strip(),
                    "pip_version": environment.pip_version,
                    "python_version": environment.python_version,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            candidates.append((EvidenceKind.ENVIRONMENT, environment_summary, None))
        for path, text in source_snippets or []:
            candidates.append((EvidenceKind.SOURCE, text, path))
        for value in previous_attempts or []:
            candidates.append((EvidenceKind.PREVIOUS_ATTEMPT, value, None))
        for value in previous_repairs or []:
            candidates.append((EvidenceKind.PREVIOUS_REPAIR, value, None))

        evidence: list[DiagnosticEvidenceItem] = []
        kind_counts: dict[EvidenceKind, int] = {}
        total = 0
        truncated = False
        for kind, raw_content, source in candidates:
            if len(evidence) >= self.limits.max_items:
                truncated = True
                break
            content = redact_sensitive_text(raw_content).strip()
            if not content:
                continue
            if len(content) > self.limits.max_item_characters:
                content = (
                    content[: self.limits.max_item_characters - 15] + "...[truncated]"
                )
                truncated = True
            remaining = self.limits.max_characters - total
            if remaining <= 0:
                truncated = True
                break
            if len(content) > remaining:
                if remaining < 100:
                    truncated = True
                    break
                content = content[: remaining - 15] + "...[truncated]"
                truncated = True
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
            evidence.append(
                DiagnosticEvidenceItem(
                    reference=f"{kind.value.lower()}:{kind_counts[kind]:03d}",
                    kind=kind,
                    content=content,
                    source=Path(source).as_posix() if source else None,
                )
            )
            total += len(content)

        return DiagnosticContext(
            failure_class=failure.failure_class,
            failure_signature=failure_signature(
                failure,
                command=command,
                environment=environment,
            ),
            evidence=evidence,
            total_characters=total,
            estimated_tokens=(total + 3) // 4,
            truncated=truncated,
        )
