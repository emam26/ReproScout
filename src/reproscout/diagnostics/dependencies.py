"""PEP-aware dependency intelligence and Docker-only pip diagnostics."""

from __future__ import annotations

import ast
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from reproscout.sandbox import Sandbox

from .models import (
    ImportDependencyFinding,
    ImportRecord,
    PackageRecord,
    PipCheckIssue,
    PipInspectReport,
    PipReport,
    RequirementParseResult,
    RequirementRecord,
)

_MAX_JSON_CHARACTERS = 2_000_000
_MAX_SOURCE_FILES = 1_000
_MAX_SOURCE_BYTES = 256 * 1024
_IMPORT_DISTRIBUTION_MAP: dict[str, tuple[str, ...]] = {
    "cv2": ("opencv-python",),
    "PIL": ("Pillow",),
    "sklearn": ("scikit-learn",),
    "yaml": ("PyYAML",),
    "bs4": ("beautifulsoup4",),
}


class DependencyDiagnosticError(RuntimeError):
    """Raised when bounded machine-readable dependency evidence is invalid."""


def version_satisfies(version: str, specifier: str) -> bool:
    """Evaluate one PEP 440 version/specifier pair through ``packaging``."""

    try:
        return Version(version) in SpecifierSet(specifier)
    except (InvalidVersion, InvalidSpecifier) as exc:
        raise DependencyDiagnosticError(
            "Invalid PEP 440 version or specifier."
        ) from exc


def requirement_marker_applies(
    requirement: RequirementRecord,
    environment: dict[str, str] | None = None,
) -> bool:
    """Evaluate a parsed PEP 508 marker through ``packaging``."""

    parsed = Requirement(requirement.original)
    return parsed.marker is None or parsed.marker.evaluate(environment=environment)


def parse_requirements_text(text: str, *, source: str) -> RequirementParseResult:
    """Parse PEP 508 requirements with ``packaging``, never handwritten syntax."""

    requirements: list[RequirementRecord] = []
    errors: list[str] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-r ", "--requirement ", "-c ", "--constraint ")):
            errors.append(
                f"line {line_number}: nested requirement files require separate inspection"
            )
            continue
        if line.startswith("-"):
            errors.append(f"line {line_number}: unsupported pip option")
            continue
        try:
            requirement = Requirement(line)
        except InvalidRequirement:
            errors.append(f"line {line_number}: invalid PEP 508 requirement")
            continue
        requirements.append(
            RequirementRecord(
                original=line,
                name=requirement.name,
                canonical_name=canonicalize_name(requirement.name),
                specifier=str(requirement.specifier),
                marker=str(requirement.marker) if requirement.marker else None,
                extras=sorted(requirement.extras),
            )
        )
    return RequirementParseResult(
        source=source,
        requirements=requirements,
        errors=errors[:100],
    )


def _load_json_object(value: str, source: str) -> dict[str, object]:
    if len(value) > _MAX_JSON_CHARACTERS:
        raise DependencyDiagnosticError(f"{source} exceeds the JSON size limit.")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise DependencyDiagnosticError(f"{source} is not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise DependencyDiagnosticError(f"{source} must be a JSON object.")
    return parsed


def _metadata_package(
    item: object,
    *,
    include_requested: bool,
    include_dependencies: bool,
) -> PackageRecord | None:
    if not isinstance(item, dict):
        return None
    metadata = item.get("metadata")
    if not isinstance(metadata, dict):
        return None
    name = metadata.get("name")
    version = metadata.get("version")
    if not isinstance(name, str) or not name.strip():
        return None
    if not isinstance(version, str) or not version.strip():
        return None
    dependencies_value = metadata.get("requires_dist")
    dependencies = (
        [str(value) for value in dependencies_value if isinstance(value, str)]
        if include_dependencies and isinstance(dependencies_value, list)
        else []
    )
    requested_value = item.get("requested")
    requested = (
        requested_value
        if include_requested and isinstance(requested_value, bool)
        else None
    )
    return PackageRecord(
        name=name,
        version=version,
        requested=requested,
        dependencies=dependencies[:1_000],
    )


def parse_pip_report(value: str) -> PipReport:
    """Parse the documented pip installation report format."""

    data = _load_json_object(value, "pip report")
    install_value = data.get("install")
    install = (
        [
            package
            for item in install_value
            if (
                package := _metadata_package(
                    item,
                    include_requested=True,
                    include_dependencies=True,
                )
            )
            is not None
        ]
        if isinstance(install_value, list)
        else []
    )
    pip_version = data.get("pip_version")
    return PipReport(
        pip_version=pip_version if isinstance(pip_version, str) else None,
        install=install[:5_000],
    )


def parse_pip_inspect(value: str) -> PipInspectReport:
    """Parse the documented ``pip inspect`` JSON format."""

    data = _load_json_object(value, "pip inspect")
    installed_value = data.get("installed")
    installed = (
        [
            package
            for item in installed_value
            if (
                package := _metadata_package(
                    item,
                    include_requested=True,
                    include_dependencies=True,
                )
            )
            is not None
        ]
        if isinstance(installed_value, list)
        else []
    )
    pip_version = data.get("pip_version")
    return PipInspectReport(
        pip_version=pip_version if isinstance(pip_version, str) else None,
        installed=installed[:5_000],
    )


_PIP_CHECK_PACKAGE = re.compile(r"^(?P<package>[A-Za-z0-9_.-]+)\s")


def parse_pip_check(value: str) -> list[PipCheckIssue]:
    """Normalize bounded human-readable ``pip check`` findings."""

    issues: list[PipCheckIssue] = []
    for raw_line in value.splitlines()[:1_000]:
        line = raw_line.strip()
        if not line or line == "No broken requirements found.":
            continue
        match = _PIP_CHECK_PACKAGE.match(line)
        issues.append(
            PipCheckIssue(
                package=match.group("package") if match else None,
                detail=line[:2_000],
            )
        )
    return issues


def _safe_workspace_file(root: Path, relative: Path) -> Path | None:
    if relative.is_absolute() or ".." in relative.parts:
        return None
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    if path.is_symlink() or not path.is_file():
        return None
    return path


def detect_python_imports(workspace: Path) -> list[ImportRecord]:
    """Detect top-level imports with AST parsing and strict source bounds."""

    root = Path(workspace).resolve()
    records: set[tuple[str, str, int]] = set()
    file_count = 0
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        directories[:] = sorted(
            directory
            for directory in directories
            if directory not in {".git", ".venv", "venv", "__pycache__"}
            and not (Path(current) / directory).is_symlink()
        )
        for filename in sorted(files):
            if not filename.endswith(".py") or file_count >= _MAX_SOURCE_FILES:
                continue
            path = _safe_workspace_file(
                root, Path(current).relative_to(root) / filename
            )
            if path is None:
                continue
            try:
                if path.stat().st_size > _MAX_SOURCE_BYTES:
                    continue
                source = path.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(source, filename=path.name)
            except (OSError, SyntaxError):
                continue
            file_count += 1
            relative = path.relative_to(root).as_posix()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [alias.name.split(".", 1)[0] for alias in node.names]
                elif (
                    isinstance(node, ast.ImportFrom) and node.module and node.level == 0
                ):
                    modules = [node.module.split(".", 1)[0]]
                else:
                    continue
                for module in modules:
                    records.add((module, relative, node.lineno))
    return [
        ImportRecord(module=module, source_path=path, line=line)
        for module, path, line in sorted(records)
    ]


def compare_imports_to_requirements(
    imports: list[ImportRecord],
    requirements: list[RequirementRecord],
) -> list[ImportDependencyFinding]:
    """Compare imports to declarations without authorizing guessed installation."""

    declared = {requirement.canonical_name for requirement in requirements}
    local_roots = {
        Path(record.source_path).parts[0].replace("-", "_")
        for record in imports
        if len(Path(record.source_path).parts) > 1
    }
    grouped: dict[str, list[str]] = {}
    for record in imports:
        grouped.setdefault(record.module, []).append(record.source_path)
    findings: list[ImportDependencyFinding] = []
    for module, paths in sorted(grouped.items()):
        if module in sys.stdlib_module_names or module in local_roots:
            continue
        mapped = _IMPORT_DISTRIBUTION_MAP.get(module)
        candidates = list(mapped or (module.replace("_", "-"),))
        is_declared = any(
            canonicalize_name(candidate) in declared for candidate in candidates
        )
        findings.append(
            ImportDependencyFinding(
                module=module,
                candidate_distributions=candidates,
                declared=is_declared,
                heuristic_mapping=mapped is not None,
                evidence_paths=sorted(set(paths))[:100],
            )
        )
    return findings


def _container_requirement_path(value: str) -> str:
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not str(path):
        raise DependencyDiagnosticError("Requirement path must remain in /workspace.")
    return str(PurePosixPath("/workspace") / path)


@dataclass(slots=True)
class PipDiagnosticRunner:
    """Run pip diagnostics only through an already-created sandbox."""

    sandbox: Sandbox
    timeout_seconds: float = 120.0

    def dry_run_requirements(self, requirements_path: str) -> PipReport:
        path = _container_requirement_path(requirements_path)
        result = self.sandbox.execute(
            [
                "python",
                "-m",
                "pip",
                "install",
                "--dry-run",
                "--ignore-installed",
                "--report",
                "-",
                "-r",
                path,
            ],
            timeout_seconds=self.timeout_seconds,
        )
        if result.exit_code != 0 or result.timed_out:
            raise DependencyDiagnosticError(
                "pip dry-run did not complete successfully."
            )
        return parse_pip_report(result.stdout)

    def inspect(self) -> PipInspectReport:
        result = self.sandbox.execute(
            ["python", "-m", "pip", "inspect"],
            timeout_seconds=self.timeout_seconds,
        )
        if result.exit_code != 0 or result.timed_out:
            raise DependencyDiagnosticError(
                "pip inspect did not complete successfully."
            )
        return parse_pip_inspect(result.stdout)

    def check(self) -> list[PipCheckIssue]:
        result = self.sandbox.execute(
            ["python", "-m", "pip", "check"],
            timeout_seconds=self.timeout_seconds,
        )
        if result.timed_out:
            raise DependencyDiagnosticError("pip check timed out.")
        return parse_pip_check(f"{result.stdout}\n{result.stderr}")
