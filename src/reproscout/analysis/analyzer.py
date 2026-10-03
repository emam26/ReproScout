"""Deterministic-first repository analysis with bounded optional interpretation."""

from __future__ import annotations

import re
import tomllib
from configparser import ConfigParser
from configparser import Error as ConfigParserError
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic import ValidationError

from reproscout.llm import LLMProvider, LLMRequest
from reproscout.repo import RepositoryManifest

from .context import ContextDocument, ContextLimits, collect_context_documents
from .models import (
    AnalysisEvidence,
    AnalysisInference,
    EvidenceProvenance,
    RepositoryAnalysis,
    ResolvedEnvironment,
)


class RepositoryAnalysisError(RuntimeError):
    """Raised when repository evidence cannot be analyzed safely."""


def _append_unique(values: list[str], value: str) -> None:
    normalized = value.strip()
    if normalized and normalized not in values:
        values.append(normalized)


@dataclass(slots=True)
class _AnalysisBuilder:
    manifest: RepositoryManifest
    project_type: str = "unknown"
    python_version_hints: list[str] = field(default_factory=list)
    package_manager: str | None = None
    dependency_sources: list[str] = field(default_factory=list)
    install_commands: list[str] = field(default_factory=list)
    test_commands: list[str] = field(default_factory=list)
    run_commands: list[str] = field(default_factory=list)
    entrypoints: list[str] = field(default_factory=list)
    environment_variables: list[str] = field(default_factory=list)
    external_assets: list[str] = field(default_factory=list)
    gpu_required: bool = False
    network_required: bool = False
    likely_execution_target: str | None = None
    evidence: list[AnalysisEvidence] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    def add_evidence(
        self,
        category: str,
        value: str,
        provenance: EvidenceProvenance,
        source_path: str | None,
        detail: str | None = None,
    ) -> None:
        evidence = AnalysisEvidence(
            category=category,
            value=value,
            provenance=provenance,
            source_path=source_path,
            detail=detail,
        )
        if evidence not in self.evidence:
            self.evidence.append(evidence)

    def has_deterministic_evidence(self, category: str) -> bool:
        return any(
            item.category == category
            and item.provenance is not EvidenceProvenance.LLM_INFERRED
            for item in self.evidence
        )

    def build(self, context_files: list[str], confidence: float) -> RepositoryAnalysis:
        resolved_environment = _resolve_environment(self)
        return RepositoryAnalysis(
            repository=f"{self.manifest.owner}/{self.manifest.repository_name}",
            repository_url=self.manifest.normalized_url,
            commit_sha=self.manifest.commit_sha,
            project_type=self.project_type,
            python_version_hints=self.python_version_hints,
            package_manager=self.package_manager,
            dependency_sources=self.dependency_sources,
            install_commands=self.install_commands,
            test_commands=self.test_commands,
            run_commands=self.run_commands,
            entrypoints=self.entrypoints,
            environment_variables=self.environment_variables,
            external_assets=self.external_assets,
            gpu_required=self.gpu_required,
            network_required=self.network_required,
            likely_execution_target=self.likely_execution_target,
            evidence=self.evidence,
            conflicts=self.conflicts,
            context_files=context_files,
            confidence=confidence,
            resolved_environment=resolved_environment,
        )


def _resolve_environment(builder: _AnalysisBuilder) -> ResolvedEnvironment:
    """Resolve only the supported v0.1 pip/CPU/Python envelope."""

    unresolved: list[str] = []
    strategies = {
        "pip" if builder.package_manager == "pip" else builder.package_manager
    }
    if builder.package_manager in {"conda", "poetry", "uv"}:
        unresolved.append(
            f"Unsupported automatic environment manager: {builder.package_manager}."
        )
    if builder.package_manager is None and builder.dependency_sources:
        unresolved.append(
            "Dependency source was detected without a supported installer."
        )
    exact_versions = {
        match.group(1)
        for hint in builder.python_version_hints
        if (
            match := re.fullmatch(r"(?:==)?(\d+\.\d+(?:\.\d+)?)", hint.replace(" ", ""))
        )
    }
    python_version = min(exact_versions) if len(exact_versions) == 1 else None
    if len(exact_versions) > 1:
        unresolved.append("Repository evidence specifies conflicting Python versions.")
    if python_version is not None:
        major, minor = (int(part) for part in python_version.split(".")[:2])
        if major != 3 or minor not in {9, 10, 11, 12, 13}:
            unresolved.append(
                f"Python {python_version} is outside the supported v0.1 runtime envelope."
            )
    if builder.gpu_required:
        unresolved.append("GPU/CUDA execution is outside the supported CPU envelope.")
    base_image = (
        f"python:{python_version}-slim" if python_version else "python:3.11-slim"
    )
    if any(
        source.lower().endswith(("environment.yml", "environment.yaml"))
        for source in builder.dependency_sources
    ):
        unresolved.append(
            "Conda environment files are detected but not automatically executed."
        )
    network_requirement = "required" if builder.network_required else "none"
    return ResolvedEnvironment(
        python_version=python_version,
        base_image=base_image,
        dependency_sources=list(builder.dependency_sources),
        install_strategy=next(iter(strategies - {None}), None),
        network_requirement=network_requirement,
        gpu_requirement=builder.gpu_required,
        unresolved_requirements=unresolved,
        evidence=[
            item
            for item in builder.evidence
            if item.category
            in {
                "python_version",
                "install_command",
                "container_base_image",
                "gpu_requirement",
            }
        ],
    )


_PYTHON_HINT = re.compile(
    r"\bpython(?:\s+version)?\s*(?P<spec>(?:==|>=|<=|~=|>|<)?\s*\d+\.\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_CI_PYTHON_HINT = re.compile(
    r"python-version\s*:\s*[\"']?(?P<version>\d+\.\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_ENV_ASSIGNMENT = re.compile(
    r"\b(?:export|set|setx)\s+(?P<name>[A-Z][A-Z0-9_]{2,})\s*=",
    re.IGNORECASE,
)
_ENV_REFERENCE = re.compile(
    r"\$\{(?P<braced>[A-Z][A-Z0-9_]{2,})\}|\$(?P<plain>[A-Z][A-Z0-9_]{2,})"
)
_URL = re.compile(r"https?://[^\s)>'\"]+", re.IGNORECASE)
_ASSET_HINT = re.compile(
    r"(?:checkpoint|weights?|model|dataset|\.ckpt\b|\.pth\b|\.pt\b|\.bin\b)",
    re.IGNORECASE,
)
_GPU_REQUIRED = re.compile(
    r"(?:requires?|required|needs?)\s+(?:an?\s+)?(?:nvidia\s+)?(?:gpu|cuda)|"
    r"(?:gpu|cuda)\s+(?:is\s+)?required|"
    r"requires?[^\n]{0,100}(?:gpu|cuda)",
    re.IGNORECASE,
)
_COMMAND_PREFIXES = (
    "pip install ",
    "python -m pip install ",
    "conda env create ",
    "poetry install",
    "uv sync",
    "pytest",
    "python -m pytest",
    "tox",
    "python ",
    "streamlit run ",
    "gradio ",
)


def _clean_command(line: str) -> str | None:
    command = line.strip().removeprefix("$").strip()
    if not command or len(command) > 1_000:
        return None
    lowered = command.lower()
    if any(lowered.startswith(prefix) for prefix in _COMMAND_PREFIXES):
        return command
    return None


def _safe_asset_url(url: str) -> str:
    parsed = urlsplit(url.rstrip(".,;"))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _record_python_hint(
    builder: _AnalysisBuilder,
    hint: str,
    provenance: EvidenceProvenance,
    source: str,
) -> None:
    normalized = hint.replace(" ", "")
    _append_unique(builder.python_version_hints, normalized)
    builder.add_evidence("python_version", normalized, provenance, source)


def _analyze_pyproject(
    builder: _AnalysisBuilder,
    document: ContextDocument,
) -> None:
    try:
        data = tomllib.loads(document.text)
    except tomllib.TOMLDecodeError:
        builder.conflicts.append(f"Could not parse {document.path} as TOML.")
        return
    project = data.get("project")
    project_data = project if isinstance(project, dict) else {}
    builder.project_type = "python"
    builder.package_manager = builder.package_manager or "pip"
    _append_unique(builder.dependency_sources, document.path)
    _append_unique(builder.install_commands, "python -m pip install .")
    builder.network_required = True
    builder.add_evidence(
        "install_command",
        "python -m pip install .",
        EvidenceProvenance.DETERMINISTICALLY_DETECTED,
        document.path,
    )
    requires_python = project_data.get("requires-python")
    if isinstance(requires_python, str):
        _record_python_hint(
            builder,
            requires_python,
            EvidenceProvenance.DETERMINISTICALLY_DETECTED,
            document.path,
        )
    scripts = project_data.get("scripts")
    if isinstance(scripts, dict):
        for name, target in sorted(scripts.items()):
            if isinstance(target, str):
                entrypoint = f"{name} = {target}"
                _append_unique(builder.entrypoints, entrypoint)
                builder.add_evidence(
                    "entrypoint",
                    entrypoint,
                    EvidenceProvenance.DETERMINISTICALLY_DETECTED,
                    document.path,
                )
    dependencies = project_data.get("dependencies")
    if isinstance(dependencies, list):
        lowered = " ".join(str(item).lower() for item in dependencies)
        if any(name in lowered for name in ("torch", "tensorflow", "jax")):
            builder.project_type = "machine_learning"
    tool = data.get("tool")
    if isinstance(tool, dict) and "pytest" in tool:
        _append_unique(builder.test_commands, "python -m pytest")
        builder.add_evidence(
            "test_command",
            "python -m pytest",
            EvidenceProvenance.DETERMINISTICALLY_DETECTED,
            document.path,
        )


def _analyze_requirements(
    builder: _AnalysisBuilder,
    document: ContextDocument,
) -> None:
    builder.project_type = (
        "python" if builder.project_type == "unknown" else builder.project_type
    )
    builder.package_manager = builder.package_manager or "pip"
    _append_unique(builder.dependency_sources, document.path)
    command = f"python -m pip install -r {document.path}"
    _append_unique(builder.install_commands, command)
    builder.network_required = True
    builder.add_evidence(
        "install_command",
        command,
        EvidenceProvenance.DETERMINISTICALLY_DETECTED,
        document.path,
    )
    if re.search(
        r"^(?:torch|tensorflow|jax)(?:[<>=~!]|$)",
        document.text,
        re.MULTILINE | re.IGNORECASE,
    ):
        builder.project_type = "machine_learning"


def _analyze_environment(
    builder: _AnalysisBuilder,
    document: ContextDocument,
) -> None:
    builder.package_manager = "conda"
    builder.project_type = (
        "python" if builder.project_type == "unknown" else builder.project_type
    )
    _append_unique(builder.dependency_sources, document.path)
    command = f"conda env create -f {document.path}"
    _append_unique(builder.install_commands, command)
    builder.network_required = True
    builder.add_evidence(
        "install_command",
        command,
        EvidenceProvenance.DETERMINISTICALLY_DETECTED,
        document.path,
    )
    match = re.search(r"-\s*python\s*=\s*([^\s#]+)", document.text, re.IGNORECASE)
    if match:
        _record_python_hint(
            builder,
            match.group(1),
            EvidenceProvenance.DETERMINISTICALLY_DETECTED,
            document.path,
        )


def _analyze_setup_metadata(
    builder: _AnalysisBuilder,
    document: ContextDocument,
) -> None:
    builder.project_type = (
        "python" if builder.project_type == "unknown" else builder.project_type
    )
    builder.package_manager = builder.package_manager or "pip"
    _append_unique(builder.dependency_sources, document.path)
    _append_unique(builder.install_commands, "python -m pip install .")
    builder.network_required = True
    builder.add_evidence(
        "install_command",
        "python -m pip install .",
        EvidenceProvenance.DETERMINISTICALLY_DETECTED,
        document.path,
    )
    if Path(document.path).name.lower() != "setup.cfg":
        return
    parser = ConfigParser()
    try:
        parser.read_string(document.text)
    except ConfigParserError:
        builder.conflicts.append(f"Could not parse {document.path} as INI.")
        return
    if parser.has_option("options", "python_requires"):
        _record_python_hint(
            builder,
            parser.get("options", "python_requires"),
            EvidenceProvenance.DETERMINISTICALLY_DETECTED,
            document.path,
        )
    if parser.has_option("options.entry_points", "console_scripts"):
        for line in parser.get("options.entry_points", "console_scripts").splitlines():
            entrypoint = line.strip()
            if entrypoint:
                _append_unique(builder.entrypoints, entrypoint)
                builder.add_evidence(
                    "entrypoint",
                    entrypoint,
                    EvidenceProvenance.DETERMINISTICALLY_DETECTED,
                    document.path,
                )


def _analyze_dockerfile(
    builder: _AnalysisBuilder,
    document: ContextDocument,
) -> None:
    for match in re.finditer(
        r"^\s*FROM\s+([^\s]+)", document.text, re.MULTILINE | re.IGNORECASE
    ):
        image = match.group(1)
        builder.add_evidence(
            "container_base_image",
            image,
            EvidenceProvenance.DETERMINISTICALLY_DETECTED,
            document.path,
        )
        python_match = re.search(r"python:(\d+\.\d+(?:\.\d+)?)", image, re.IGNORECASE)
        if python_match:
            _record_python_hint(
                builder,
                python_match.group(1),
                EvidenceProvenance.DETERMINISTICALLY_DETECTED,
                document.path,
            )
        if "cuda" in image.lower():
            builder.gpu_required = True
            builder.add_evidence(
                "gpu_requirement",
                image,
                EvidenceProvenance.DETERMINISTICALLY_DETECTED,
                document.path,
            )


def _analyze_documentation(
    builder: _AnalysisBuilder,
    document: ContextDocument,
) -> None:
    for match in _PYTHON_HINT.finditer(document.text):
        _record_python_hint(
            builder,
            match.group("spec"),
            EvidenceProvenance.DOCUMENTED,
            document.path,
        )
    for line in document.text.splitlines():
        command = _clean_command(line)
        if command is None:
            continue
        lowered = command.lower()
        if "install" in lowered or lowered == "uv sync":
            _append_unique(builder.install_commands, command)
            category = "install_command"
            builder.network_required = True
        elif lowered.startswith(("pytest", "python -m pytest", "tox")):
            _append_unique(builder.test_commands, command)
            category = "test_command"
        else:
            _append_unique(builder.run_commands, command)
            category = "run_command"
        builder.add_evidence(
            category,
            command,
            EvidenceProvenance.DOCUMENTED,
            document.path,
        )
    for match in _ENV_ASSIGNMENT.finditer(document.text):
        _append_unique(builder.environment_variables, match.group("name").upper())
    for match in _ENV_REFERENCE.finditer(document.text):
        _append_unique(
            builder.environment_variables,
            (match.group("braced") or match.group("plain")).upper(),
        )
    for url_match in _URL.finditer(document.text):
        url = _safe_asset_url(url_match.group(0))
        if _ASSET_HINT.search(url) or _ASSET_HINT.search(
            document.text[max(0, url_match.start() - 60) : url_match.end() + 60]
        ):
            _append_unique(builder.external_assets, url)
            builder.network_required = True
            builder.add_evidence(
                "external_asset",
                url,
                EvidenceProvenance.DOCUMENTED,
                document.path,
            )
    if _GPU_REQUIRED.search(document.text):
        builder.gpu_required = True
        builder.add_evidence(
            "gpu_requirement",
            "GPU/CUDA required by documentation",
            EvidenceProvenance.DOCUMENTED,
            document.path,
        )


def _analyze_support_file(
    builder: _AnalysisBuilder,
    document: ContextDocument,
) -> None:
    name = Path(document.path).name.lower()
    if name in {"pytest.ini", "tox.ini"}:
        _append_unique(builder.test_commands, "python -m pytest")
        builder.add_evidence(
            "test_command",
            "python -m pytest",
            EvidenceProvenance.DETERMINISTICALLY_DETECTED,
            document.path,
        )
    if name == "makefile":
        for target in ("test", "demo", "run"):
            if re.search(rf"^{target}\s*:", document.text, re.MULTILINE):
                command = f"make {target}"
                destination = (
                    builder.test_commands if target == "test" else builder.run_commands
                )
                _append_unique(destination, command)
                builder.add_evidence(
                    "test_command" if target == "test" else "run_command",
                    command,
                    EvidenceProvenance.DETERMINISTICALLY_DETECTED,
                    document.path,
                )


def _analyze_ci(builder: _AnalysisBuilder, document: ContextDocument) -> None:
    for match in _CI_PYTHON_HINT.finditer(document.text):
        _record_python_hint(
            builder,
            match.group("version"),
            EvidenceProvenance.DETERMINISTICALLY_DETECTED,
            document.path,
        )
    for line in document.text.splitlines():
        stripped = line.strip().removeprefix("-").strip()
        command = _clean_command(stripped.removeprefix("run:").strip())
        if command is None:
            continue
        if command.lower().startswith(("pytest", "python -m pytest", "tox")):
            _append_unique(builder.test_commands, command)
            category = "test_command"
        elif "install" in command.lower():
            _append_unique(builder.install_commands, command)
            category = "install_command"
            builder.network_required = True
        else:
            continue
        builder.add_evidence(
            category,
            command,
            EvidenceProvenance.DETERMINISTICALLY_DETECTED,
            document.path,
        )


def _detect_python_conflicts(builder: _AnalysisBuilder) -> None:
    documented = {
        evidence.value
        for evidence in builder.evidence
        if evidence.category == "python_version"
        and evidence.provenance is EvidenceProvenance.DOCUMENTED
    }
    detected = {
        evidence.value
        for evidence in builder.evidence
        if evidence.category == "python_version"
        and evidence.provenance is EvidenceProvenance.DETERMINISTICALLY_DETECTED
    }
    for documented_hint in sorted(documented):
        documented_version = re.search(r"\d+\.\d+", documented_hint)
        if documented_version is None:
            continue
        for detected_hint in sorted(detected):
            minimum = re.match(r">=?\s*(\d+\.\d+)", detected_hint)
            if minimum and tuple(
                map(int, documented_version.group().split("."))
            ) < tuple(map(int, minimum.group(1).split("."))):
                message = (
                    f"Documentation suggests Python {documented_hint}, while project "
                    f"metadata declares {detected_hint}."
                )
                _append_unique(builder.conflicts, message)


def _deterministic_analysis(
    manifest: RepositoryManifest,
    documents: list[ContextDocument],
) -> _AnalysisBuilder:
    builder = _AnalysisBuilder(manifest)
    for document in documents:
        name = Path(document.path).name.lower()
        lowered_parts = tuple(part.lower() for part in Path(document.path).parts)
        if name == "pyproject.toml":
            _analyze_pyproject(builder, document)
        elif name.startswith("requirements") and name.endswith(".txt"):
            _analyze_requirements(builder, document)
        elif name in {"environment.yml", "environment.yaml"}:
            _analyze_environment(builder, document)
        elif name in {"setup.py", "setup.cfg"}:
            _analyze_setup_metadata(builder, document)
        elif name.startswith("dockerfile"):
            _analyze_dockerfile(builder, document)
        elif name.startswith(("readme", "install")):
            _analyze_documentation(builder, document)
        elif len(lowered_parts) >= 3 and lowered_parts[-3:-1] == (
            ".github",
            "workflows",
        ):
            _analyze_ci(builder, document)
        else:
            _analyze_support_file(builder, document)
    if builder.run_commands:
        builder.likely_execution_target = builder.run_commands[0]
    elif builder.test_commands:
        builder.likely_execution_target = builder.test_commands[0]
    elif builder.entrypoints:
        builder.likely_execution_target = builder.entrypoints[0].split(" = ", 1)[0]
    _detect_python_conflicts(builder)
    return builder


def _apply_inference(
    builder: _AnalysisBuilder,
    inference: AnalysisInference,
    summary: str,
) -> None:
    additions = {
        "install_command": (builder.install_commands, inference.install_commands),
        "test_command": (builder.test_commands, inference.test_commands),
        "run_command": (builder.run_commands, inference.run_commands),
        "environment_variable": (
            builder.environment_variables,
            inference.environment_variables,
        ),
        "external_asset": (builder.external_assets, inference.external_assets),
    }
    for category, (destination, values) in additions.items():
        for value in values:
            _append_unique(destination, value)
            builder.add_evidence(
                category,
                value,
                EvidenceProvenance.LLM_INFERRED,
                None,
                summary,
            )
    if builder.project_type == "unknown" and inference.project_type:
        builder.project_type = inference.project_type
        builder.add_evidence(
            "project_type",
            inference.project_type,
            EvidenceProvenance.LLM_INFERRED,
            None,
            summary,
        )
    if builder.likely_execution_target is None and inference.likely_execution_target:
        builder.likely_execution_target = inference.likely_execution_target
        builder.add_evidence(
            "likely_execution_target",
            inference.likely_execution_target,
            EvidenceProvenance.LLM_INFERRED,
            None,
            summary,
        )
    if (
        not builder.has_deterministic_evidence("gpu_requirement")
        and inference.gpu_required is not None
    ):
        builder.gpu_required = inference.gpu_required
    if (
        not builder.has_deterministic_evidence("external_asset")
        and inference.network_required is not None
    ):
        builder.network_required = inference.network_required


class RepositoryAnalyzer:
    """Build deterministic analysis and optionally resolve ambiguity through an LLM."""

    def __init__(self, context_limits: ContextLimits | None = None) -> None:
        self.context_limits = context_limits or ContextLimits()

    async def analyze(
        self,
        manifest: RepositoryManifest,
        *,
        provider: LLMProvider | None = None,
    ) -> RepositoryAnalysis:
        documents = collect_context_documents(manifest, self.context_limits)
        builder = _deterministic_analysis(manifest, documents)
        confidence = 0.9 if builder.likely_execution_target else 0.65
        needs_interpretation = (
            bool(builder.conflicts) or builder.likely_execution_target is None
        )
        if provider is not None and needs_interpretation:
            preliminary = builder.build(
                [document.path for document in documents],
                confidence,
            )
            response = await provider.decide(
                LLMRequest(
                    context={
                        "analysis": preliminary.model_dump(mode="json"),
                        "documents": [
                            {"path": document.path, "text": document.text}
                            for document in documents
                        ],
                    },
                    available_actions=["supplement_analysis"],
                    system_instruction=(
                        "Resolve only ambiguity in repository-local evidence. Do not "
                        "overwrite deterministic facts or invent requirements. Return "
                        "action supplement_analysis with an AnalysisInference object "
                        "as arguments."
                    ),
                )
            )
            if response.decision.action != "supplement_analysis":
                raise RepositoryAnalysisError(
                    "LLM analysis returned an unsupported action."
                )
            try:
                inference = AnalysisInference.model_validate(
                    response.decision.arguments
                )
            except ValidationError as exc:
                raise RepositoryAnalysisError(
                    "LLM analysis inference failed schema validation."
                ) from exc
            _apply_inference(builder, inference, response.decision.summary)
            confidence = max(confidence, inference.confidence)
        return builder.build(
            [document.path for document in documents],
            confidence,
        )
