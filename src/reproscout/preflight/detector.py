"""Deterministic repository-side environment prerequisite detection."""

from __future__ import annotations

import ast
import configparser
import re
import shlex
import tomllib
from pathlib import Path

from reproscout.analysis import AnalysisEvidence, RepositoryAnalysis
from reproscout.diagnostics import (
    AssetKind,
    AssetReference,
    RequirementRecord,
    detect_repository_assets,
    parse_requirements_text,
)
from reproscout.planning.safety import PlanSafetyError, validate_command
from reproscout.repo import RepositoryManifest
from reproscout.security import (
    BoundedReadError,
    read_bounded_workspace_text,
    redact_sensitive_text,
)

from .models import (
    PreflightInspection,
    PreflightRequirement,
    RequirementKind,
    RequirementState,
)

_MAX_DEPENDENCY_FILE_BYTES = 256 * 1024
_MAX_REQUIREMENT_RECORDS = 2_000
_MAX_UNSAFE_FINDING_CHARS = 2_000
_KNOWN_SYSTEM_TOOLS = frozenset(
    {
        "7z",
        "cmake",
        "conda",
        "curl",
        "ffmpeg",
        "gcc",
        "g++",
        "git",
        "java",
        "make",
        "node",
        "npm",
        "nvidia-smi",
        "nvcc",
        "rsync",
        "tar",
        "unzip",
        "wget",
    }
)
_SAFE_TOOL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+@-]*$")
_SHELL_COMMAND_START = re.compile(
    r"^(?:\$\s*)?(?:sudo|su|rm|docker|apt|apt-get|apk|dnf|yum|pacman|"
    r"brew|choco|winget|curl|wget|python|pytest|tox|make|cmake|ffmpeg|"
    r"gcc|g\+\+|conda|uv|poetry|bash|sh|git|node|npm|java|nvcc|"
    r"nvidia-smi)\b",
    re.IGNORECASE,
)
_STANDARD_ENVIRONMENT_VARIABLES = frozenset(
    {"HOME", "PATH", "PWD", "OLDPWD", "USER", "SHELL", "TERM", "LANG"}
)
_INCOMPATIBLE_MARKERS = (
    "outside the supported",
    "conflicting python",
    "unsupported automatic environment manager",
    "unsupported v0.1 runtime",
)


def _append_unique(values: list[str], value: str) -> None:
    normalized = value.strip()
    if normalized and normalized not in values:
        values.append(normalized)


def _read_file(manifest: RepositoryManifest, relative: str) -> str | None:
    try:
        return read_bounded_workspace_text(
            manifest.workspace_path,
            relative,
            max_bytes=_MAX_DEPENDENCY_FILE_BYTES,
        )
    except (BoundedReadError, OSError):
        return None


def _requirement_records(
    values: list[str],
    *,
    source: str,
) -> tuple[list[RequirementRecord], list[str]]:
    parsed = parse_requirements_text("\n".join(values), source=source)
    return parsed.requirements, parsed.errors


def _pyproject_requirements(
    text: str,
    *,
    source: str,
) -> tuple[list[RequirementRecord], list[RequirementRecord], list[str]]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return [], [], [f"{source}: invalid TOML"]

    values: list[str] = []
    optional_values: list[str] = []
    project = data.get("project")
    if isinstance(project, dict):
        dependencies = project.get("dependencies")
        if isinstance(dependencies, list):
            values.extend(item for item in dependencies if isinstance(item, str))
            if not all(isinstance(item, str) for item in dependencies):
                return [], [], [
                    f"{source}: project.dependencies contains a non-string value"
                ]
        elif dependencies is not None:
            return [], [], [f"{source}: project.dependencies is not a string list"]
        dynamic = project.get("dynamic")
        if isinstance(dynamic, list) and "dependencies" in dynamic:
            return [], [], [f"{source}: project.dependencies is dynamically defined"]
        optional_dependencies = project.get("optional-dependencies")
        if isinstance(optional_dependencies, dict):
            for group, group_values in optional_dependencies.items():
                if not isinstance(group, str) or not isinstance(group_values, list):
                    return [], [], [
                        f"{source}: optional-dependencies group is not a string list"
                    ]
                if not all(isinstance(item, str) for item in group_values):
                    return [], [], [
                        (
                            f"{source}: optional-dependencies group {group!r} "
                            "contains a non-string requirement"
                        )
                    ]
                optional_values.extend(group_values)
        elif optional_dependencies is not None:
            return [], [], [
                f"{source}: project.optional-dependencies is not a table"
            ]
    build_system = data.get("build-system")
    if isinstance(build_system, dict):
        requires = build_system.get("requires")
        if isinstance(requires, list):
            values.extend(item for item in requires if isinstance(item, str))
            if not all(isinstance(item, str) for item in requires):
                return [], [], [
                    f"{source}: build-system.requires contains a non-string value"
                ]
        elif requires is not None:
            return [], [], [f"{source}: build-system.requires is not a string list"]
    required, required_errors = _requirement_records(values, source=source)
    optional, optional_errors = _requirement_records(optional_values, source=source)
    return required, optional, [*required_errors, *optional_errors]


def _setup_cfg_requirements(
    text: str,
    *,
    source: str,
) -> tuple[list[RequirementRecord], list[str]]:
    parser = configparser.RawConfigParser()
    try:
        parser.read_string(text)
    except configparser.Error:
        return [], [f"{source}: invalid INI"]
    if not parser.has_option("options", "install_requires"):
        return [], []
    raw = parser.get("options", "install_requires")
    values = [line.strip() for line in raw.splitlines() if line.strip()]
    return _requirement_records(values, source=source)


def _setup_py_requirements(
    text: str,
    *,
    source: str,
) -> tuple[list[RequirementRecord], list[str]]:
    """Read literal ``install_requires`` from setup.py without importing it."""

    try:
        tree = ast.parse(text, filename=source)
    except SyntaxError:
        return [], [f"{source}: invalid Python syntax"]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            function_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            function_name = node.func.attr
        else:
            function_name = None
        if function_name != "setup":
            continue
        for keyword in node.keywords:
            if keyword.arg != "install_requires":
                continue
            try:
                values = ast.literal_eval(keyword.value)
            except (ValueError, TypeError, SyntaxError):
                return [], [f"{source}: install_requires is dynamic"]
            if not isinstance(values, list) or not all(
                isinstance(item, str) for item in values
            ):
                return [], [f"{source}: install_requires is not a string list"]
            return _requirement_records(values, source=source)
    return [], [f"{source}: install_requires was not statically found"]


def _environment_requirements(
    text: str,
    *,
    source: str,
) -> tuple[list[RequirementRecord], list[str]]:
    """Parse only simple conda entries; the conda environment is not executed."""

    values: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("-") or line.startswith("--"):
            continue
        value = line[1:].strip()
        if not value or value.startswith("python="):
            continue
        if value.startswith("pip:"):
            continue
        if re.fullmatch(r"[A-Za-z0-9_.+\-]+(?:[<>=!~].*)?", value):
            values.append(value.replace("=", "==", 1) if "=" in value else value)
    records, errors = _requirement_records(values, source=source)
    return records, errors


def _detect_system_tools(documents: list[tuple[str, str]]) -> list[str]:
    tools: list[str] = []
    for _, text in documents:
        for raw_line in text.splitlines():
            line = raw_line.strip().removeprefix("$").strip().strip("`")
            if not line:
                continue
            apt_match = re.search(
                r"\b(?:apt-get|apt|apk|dnf|yum|pacman)\s+"
                r"(?:[^;&|]*\s)?install\s+(?P<packages>[^;&|]+)",
                line,
                re.IGNORECASE,
            )
            if apt_match:
                for token in re.split(r"\s+", apt_match.group("packages")):
                    token = token.strip("'\"(),")
                    if token.startswith("-") or not _SAFE_TOOL_NAME.fullmatch(token):
                        continue
                    if token in _KNOWN_SYSTEM_TOOLS:
                        _append_unique(tools, token)
            try:
                tokens = shlex.split(line, comments=True, posix=True)
            except ValueError:
                tokens = []
            if tokens and tokens[0] in _KNOWN_SYSTEM_TOOLS:
                _append_unique(tools, tokens[0])
            for tool in _KNOWN_SYSTEM_TOOLS:
                if re.search(
                    rf"\b{re.escape(tool)}\b[^\n]{{0,80}}\b(?:required|needed|must)\b",
                    line,
                    re.IGNORECASE,
                ):
                    _append_unique(tools, tool)
    return sorted(tools)


def _unsafe_findings(documents: list[tuple[str, str]]) -> list[str]:
    findings: list[str] = []
    for source, text in documents:
        for line_number, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip().removeprefix("$").strip().strip("`")
            if not _SHELL_COMMAND_START.match(line):
                continue
            try:
                validate_command(line, max_length=2_000)
            except PlanSafetyError as exc:
                displayed = line[:_MAX_UNSAFE_FINDING_CHARS]
                if len(line) > _MAX_UNSAFE_FINDING_CHARS:
                    displayed += "..."
                finding = redact_sensitive_text(
                    f"{source}:{line_number}: Untrusted repository command "
                    f"(not executed): {displayed!r}; safety policy: {exc}"
                )
                _append_unique(findings, finding)
    return findings


def _is_incompatible(detail: str) -> bool:
    lowered = detail.lower()
    return any(marker in lowered for marker in _INCOMPATIBLE_MARKERS)


def _source_evidence(
    analysis: RepositoryAnalysis,
    category: str,
) -> list[AnalysisEvidence]:
    return [item for item in analysis.evidence if item.category == category]


def inspect_environment(
    manifest: RepositoryManifest,
    analysis: RepositoryAnalysis,
) -> PreflightInspection:
    """Inspect setup declarations without executing repository code."""

    documents: list[tuple[str, str]] = []
    for relative in sorted(
        {
            *manifest.documentation_files,
            *manifest.dependency_files,
            *manifest.dockerfile_indicators,
            *manifest.ci_workflow_indicators,
        }
    ):
        text = _read_file(manifest, relative)
        if text is not None:
            documents.append((relative, text))

    package_requirements: list[RequirementRecord] = []
    optional_package_requirements: list[RequirementRecord] = []
    parse_errors: list[str] = []
    for relative in manifest.dependency_files:
        text = _read_file(manifest, relative)
        if text is None:
            parse_errors.append(f"Could not read dependency file {relative}.")
            continue
        name = Path(relative).name.lower()
        if name == "pyproject.toml":
            records, optional_records, errors = _pyproject_requirements(
                text, source=relative
            )
        elif name == "setup.cfg":
            records, errors = _setup_cfg_requirements(text, source=relative)
            optional_records = []
        elif name in {"environment.yml", "environment.yaml"}:
            records, errors = _environment_requirements(text, source=relative)
            optional_records = []
        elif name == "setup.py":
            records, errors = _setup_py_requirements(text, source=relative)
            optional_records = []
        else:
            parsed = parse_requirements_text(text, source=relative)
            records, errors = parsed.requirements, parsed.errors
            optional_records = []
        for record in records:
            if (
                record not in package_requirements
                and len(package_requirements) < _MAX_REQUIREMENT_RECORDS
            ):
                package_requirements.append(record)
        parse_errors.extend(f"{relative}: {error}" for error in errors)
        for record in optional_records:
            if (
                record not in optional_package_requirements
                and len(optional_package_requirements) < _MAX_REQUIREMENT_RECORDS
            ):
                optional_package_requirements.append(record)

    python_constraints = list(analysis.python_version_hints)
    system_tools = _detect_system_tools(documents)
    if analysis.package_manager == "conda":
        _append_unique(system_tools, "conda")
    system_tools = sorted(system_tools)

    environment_variables = sorted(
        {
            name.upper()
            for name in analysis.environment_variables
            if name.upper() not in _STANDARD_ENVIRONMENT_VARIABLES
        }
    )
    asset_scan = detect_repository_assets(manifest.workspace_path)
    analysis_assets = {
        redact_sensitive_text(value) for value in analysis.external_assets
    }
    assets: list[AssetReference] = []
    for reference in asset_scan.references:
        if reference.kind is AssetKind.EXTERNAL_URL and (
            reference.value not in analysis_assets
        ):
            continue
        assets.append(reference)
    for value in sorted(analysis_assets):
        if not any(item.value == value for item in assets):
            assets.append(
                AssetReference(
                    kind=AssetKind.EXTERNAL_URL,
                    value=value,
                    source_path="analysis",
                )
            )

    incompatibilities: list[str] = []
    missing_prerequisites: list[str] = []
    if any(
        "unsupported pip option" in error or "nested requirement files" in error
        for error in parse_errors
    ):
        _append_unique(
            missing_prerequisites,
            "A dependency file contains an unsupported pip directive; preflight "
            "refused to execute that source automatically.",
        )
    for detail in [
        *analysis.conflicts,
        *(
            analysis.resolved_environment.unresolved_requirements
            if analysis.resolved_environment is not None
            else []
        ),
    ]:
        if _is_incompatible(detail):
            _append_unique(incompatibilities, detail)
        else:
            _append_unique(missing_prerequisites, detail)

    for variable in environment_variables:
        _append_unique(
            missing_prerequisites,
            "Required environment variable "
            f"{variable} was detected but no value was supplied.",
        )
    for reference in assets:
        if reference.kind in {
            AssetKind.EXTERNAL_URL,
            AssetKind.GIT_LFS_POINTER,
            AssetKind.GIT_SUBMODULE,
            AssetKind.DVC,
        }:
            _append_unique(
                missing_prerequisites,
                f"External or deferred asset is not fetched: {reference.value}.",
            )

    unverified: list[str] = []
    if manifest.has_dockerfile:
        _append_unique(
            unverified,
            "A repository Dockerfile was detected but was not used by the "
            "default preflight image.",
        )
    if analysis.run_commands or analysis.test_commands:
        _append_unique(
            unverified,
            "Detected demo, test, or experiment commands were intentionally "
            "not executed.",
        )
    if parse_errors:
        _append_unique(
            unverified,
            "Some dependency declarations could not be statically parsed.",
        )
    if optional_package_requirements:
        _append_unique(
            unverified,
            "Optional dependency declarations were detected but were not installed "
            "by the default preflight plan.",
        )
    if manifest.dependency_files:
        _append_unique(
            unverified,
            "Dependency installation may execute repository build or metadata hooks "
            "inside Docker; it is not scientific workflow verification.",
        )
    if not manifest.dependency_files:
        _append_unique(
            unverified,
            "No supported dependency manifest was detected; package readiness "
            "is unverified.",
        )

    requirements: list[PreflightRequirement] = []
    requirements.append(
        PreflightRequirement(
            kind=RequirementKind.PYTHON,
            name="python",
            expected=(
                ", ".join(python_constraints) or "supported Python 3.11 preflight image"
            ),
            state=(
                RequirementState.INCOMPATIBLE
                if incompatibilities
                else RequirementState.UNVERIFIED
            ),
            source_paths=[
                item.source_path
                for item in _source_evidence(analysis, "python_version")
                if item.source_path is not None
            ],
        )
    )
    requirements.extend(
        PreflightRequirement(
            kind=RequirementKind.PACKAGE,
            name=record.name,
            expected=record.specifier or "any compatible version",
            state=RequirementState.UNVERIFIED,
            source_paths=[record_source]
            if (
                record_source := next(
                    (
                        source
                        for source in manifest.dependency_files
                        if record.original in (_read_file(manifest, source) or "")
                    ),
                    None,
                )
            )
            else [],
        )
        for record in package_requirements
    )
    requirements.extend(
        PreflightRequirement(
            kind=RequirementKind.PACKAGE,
            name=record.name,
            expected=record.specifier or "any compatible version",
            state=RequirementState.UNVERIFIED,
            optional=True,
            detail=(
                "Declared in an optional dependency group; not installed by default."
            ),
            source_paths=[record_source]
            if (
                record_source := next(
                    (
                        source
                        for source in manifest.dependency_files
                        if record.original in (_read_file(manifest, source) or "")
                    ),
                    None,
                )
            )
            else [],
        )
        for record in optional_package_requirements
    )
    requirements.extend(
        PreflightRequirement(
            kind=RequirementKind.SYSTEM_TOOL,
            name=tool,
            expected="available on PATH inside the preflight container",
            state=RequirementState.UNVERIFIED,
        )
        for tool in system_tools
    )
    requirements.extend(
        PreflightRequirement(
            kind=RequirementKind.ENVIRONMENT_VARIABLE,
            name=variable,
            expected="a user-supplied value",
            state=RequirementState.MISSING,
            detail="Values are intentionally not inferred or copied from the host.",
        )
        for variable in environment_variables
    )
    requirements.extend(
        PreflightRequirement(
            kind=RequirementKind.ASSET,
            name=reference.value[:300],
            expected=reference.kind.value,
            state=(
                RequirementState.MISSING
                if reference.kind
                in {
                    AssetKind.EXTERNAL_URL,
                    AssetKind.GIT_LFS_POINTER,
                    AssetKind.GIT_SUBMODULE,
                    AssetKind.DVC,
                }
                else RequirementState.DETECTED
            ),
            source_paths=[reference.source_path],
        )
        for reference in assets
    )
    if analysis.gpu_required:
        requirements.append(
            PreflightRequirement(
                kind=RequirementKind.GPU,
                name="GPU/CUDA",
                expected="compatible GPU runtime",
                state=RequirementState.MISSING,
                detail="The default preflight is CPU-only.",
            )
        )

    return PreflightInspection(
        dependency_sources=list(manifest.dependency_files),
        package_requirements=package_requirements,
        optional_package_requirements=optional_package_requirements,
        python_constraints=python_constraints,
        system_tools=system_tools,
        environment_variables=environment_variables,
        assets=assets,
        requirements=requirements,
        dependency_parse_errors=parse_errors,
        incompatibilities=incompatibilities,
        missing_prerequisites=missing_prerequisites,
        unverified_capabilities=unverified,
        unsafe_findings=_unsafe_findings(documents),
        source_files=[path for path, _ in documents],
    )
