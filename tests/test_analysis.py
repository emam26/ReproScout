from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from reproscout.analysis import (
    ContextLimits,
    EvidenceProvenance,
    RepositoryAnalysisError,
    RepositoryAnalyzer,
    collect_context_documents,
)
from reproscout.llm import AgentDecision, MockLLMProvider
from reproscout.repo import RepositoryManifest


def _manifest(root: Path, files: dict[str, str | bytes]) -> RepositoryManifest:
    for relative, contents in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(contents, bytes):
            path.write_bytes(contents)
        else:
            path.write_text(contents, encoding="utf-8")
    paths = sorted(files)
    documentation = [
        path
        for path in paths
        if Path(path).name.lower().startswith(("readme", "install"))
    ]
    dependencies = [
        path
        for path in paths
        if Path(path).name.lower().startswith("requirements")
        or Path(path).name.lower()
        in {"pyproject.toml", "setup.py", "setup.cfg", "environment.yml"}
    ]
    workflows = [path for path in paths if path.startswith(".github/workflows/")]
    dockerfiles = [
        path for path in paths if Path(path).name.lower().startswith("dockerfile")
    ]
    return RepositoryManifest(
        repository_url="https://github.com/example/project",
        normalized_url="https://github.com/example/project",
        owner="example",
        repository_name="project",
        commit_sha="a" * 40,
        workspace_path=root,
        important_files=paths,
        documentation_files=documentation,
        dependency_files=dependencies,
        ci_workflow_indicators=workflows,
        dockerfile_indicators=dockerfiles,
        has_tests=any(path.startswith("tests/") for path in paths),
        has_dockerfile=bool(dockerfiles),
        has_ci_workflows=bool(workflows),
    )


def _analyze(manifest: RepositoryManifest, provider=None):
    return asyncio.run(RepositoryAnalyzer().analyze(manifest, provider=provider))


def test_requirements_project_is_analyzed_deterministically(tmp_path: Path) -> None:
    manifest = _manifest(
        tmp_path,
        {
            "requirements.txt": "requests==2.32.0\n",
            "pytest.ini": "[pytest]\ntestpaths = tests\n",
        },
    )

    analysis = _analyze(manifest)

    assert analysis.project_type == "python"
    assert analysis.package_manager == "pip"
    assert analysis.install_commands == ["python -m pip install -r requirements.txt"]
    assert analysis.test_commands == ["python -m pytest"]
    assert analysis.network_required is True
    assert all(
        evidence.provenance is EvidenceProvenance.DETERMINISTICALLY_DETECTED
        for evidence in analysis.evidence
    )


def test_pyproject_extracts_python_entrypoint_and_test_configuration(
    tmp_path: Path,
) -> None:
    manifest = _manifest(
        tmp_path,
        {
            "pyproject.toml": """
                [project]
                name = "fixture"
                requires-python = ">=3.11"
                dependencies = ["torch>=2"]

                [project.scripts]
                fixture = "fixture.cli:main"

                [tool.pytest.ini_options]
                testpaths = ["tests"]
            """,
        },
    )

    analysis = _analyze(manifest)

    assert analysis.project_type == "machine_learning"
    assert analysis.python_version_hints == [">=3.11"]
    assert analysis.entrypoints == ["fixture = fixture.cli:main"]
    assert analysis.test_commands == ["python -m pytest"]
    assert analysis.likely_execution_target == "python -m pytest"


def test_readme_extracts_documented_workflow_environment_assets_and_gpu(
    tmp_path: Path,
) -> None:
    manifest = _manifest(
        tmp_path,
        {
            "README.md": """
                # Fixture
                Requires Python 3.10 and a CUDA GPU.

                python -m pip install -r requirements.txt
                export MODEL_CACHE=/tmp/models
                export API_KEY=literal-secret-that-must-not-reach-context
                python demo.py --checkpoint model.ckpt

                Download checkpoint: https://example.com/models/model.ckpt?token=secret
            """,
        },
    )

    analysis = _analyze(manifest)
    documents = collect_context_documents(manifest)

    assert analysis.python_version_hints == ["3.10"]
    assert analysis.run_commands == ["python demo.py --checkpoint model.ckpt"]
    assert analysis.environment_variables == ["MODEL_CACHE", "API_KEY"]
    assert analysis.external_assets == ["https://example.com/models/model.ckpt"]
    assert analysis.gpu_required is True
    assert "literal-secret" not in documents[0].text
    assert "<redacted>" in documents[0].text
    assert any(
        evidence.provenance is EvidenceProvenance.DOCUMENTED
        for evidence in analysis.evidence
    )


def test_dockerfile_and_ci_supply_runtime_and_test_hints(tmp_path: Path) -> None:
    manifest = _manifest(
        tmp_path,
        {
            "Dockerfile": "FROM nvidia/cuda:12.4-runtime\n",
            ".github/workflows/test.yml": """
                jobs:
                  test:
                    strategy:
                      matrix:
                        python-version: '3.12'
                    steps:
                      - run: python -m pytest
            """,
        },
    )

    analysis = _analyze(manifest)

    assert analysis.gpu_required is True
    assert analysis.python_version_hints == ["3.12"]
    assert analysis.test_commands == ["python -m pytest"]


def test_setup_cfg_is_parsed_without_executing_setup_code(tmp_path: Path) -> None:
    manifest = _manifest(
        tmp_path,
        {
            "setup.cfg": """
                [metadata]
                name = fixture
                [options]
                python_requires = >=3.9
                [options.entry_points]
                console_scripts =
                    fixture = fixture.cli:main
            """,
        },
    )

    analysis = _analyze(manifest)

    assert analysis.install_commands == ["python -m pip install ."]
    assert analysis.python_version_hints == [">=3.9"]
    assert analysis.entrypoints == ["fixture = fixture.cli:main"]


def test_conflicting_documentation_is_retained_not_silently_overwritten(
    tmp_path: Path,
) -> None:
    manifest = _manifest(
        tmp_path,
        {
            "pyproject.toml": "[project]\nname='x'\nrequires-python='>=3.11'\n",
            "README.md": "Use Python 3.8.\npython -m pytest\n",
        },
    )

    analysis = _analyze(manifest)

    assert set(analysis.python_version_hints) == {">=3.11", "3.8"}
    assert analysis.conflicts == [
        "Documentation suggests Python 3.8, while project metadata declares >=3.11."
    ]
    assert any(
        evidence.provenance is EvidenceProvenance.DOCUMENTED
        for evidence in analysis.evidence
        if evidence.category == "python_version"
    )


def test_context_packet_skips_binary_large_and_git_files(tmp_path: Path) -> None:
    manifest = _manifest(
        tmp_path,
        {
            "README.md": "A" * 100,
            "README.bin": b"text\x00binary",
            ".git/README.md": "must not be included",
            "requirements.txt": "requests\n",
        },
    )

    documents = collect_context_documents(
        manifest,
        ContextLimits(max_files=5, max_file_bytes=50, max_total_characters=50),
    )

    assert [document.path for document in documents] == ["requirements.txt"]


def test_mock_provider_supplements_only_ambiguous_analysis(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, {"README.md": "A small unusual project."})
    decision = AgentDecision(
        summary="The README names a runnable module but gives no shell command.",
        action="supplement_analysis",
        arguments={
            "project_type": "research_prototype",
            "likely_execution_target": "python -m fixture",
            "run_commands": ["python -m fixture"],
            "confidence": 0.7,
        },
        expected_effect="Provide an explicit low-confidence execution hypothesis.",
        confidence=0.7,
    )
    provider = MockLLMProvider([decision])

    analysis = _analyze(manifest, provider)

    assert provider.call_count == 1
    assert analysis.project_type == "research_prototype"
    assert analysis.run_commands == ["python -m fixture"]
    assert analysis.likely_execution_target == "python -m fixture"
    assert any(
        evidence.provenance is EvidenceProvenance.LLM_INFERRED
        for evidence in analysis.evidence
    )


def test_invalid_llm_analysis_action_is_rejected(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, {"README.md": "No runnable instructions."})
    provider = MockLLMProvider(
        [
            AgentDecision(
                summary="Unsupported action.",
                action="run_command",
                arguments={},
                expected_effect="Would violate analysis boundaries.",
                confidence=0.5,
            )
        ]
    )

    with pytest.raises(RepositoryAnalysisError, match="unsupported action"):
        _analyze(manifest, provider)
