from __future__ import annotations

import pytest

from reproscout.analysis import (
    AnalysisEvidence,
    EvidenceProvenance,
    RepositoryAnalysis,
)
from reproscout.planning import (
    PlanActionType,
    PlanBaseline,
    PlanLimitError,
    PlannerLimits,
    PlanProvenance,
    ReproductionPlanner,
    RiskLevel,
    UnsafePlanError,
)


def _evidence(
    category: str,
    value: str,
    provenance: EvidenceProvenance,
    source: str,
) -> AnalysisEvidence:
    return AnalysisEvidence(
        category=category,
        value=value,
        provenance=provenance,
        source_path=source,
    )


def _analysis(**overrides: object) -> RepositoryAnalysis:
    values: dict[str, object] = {
        "repository": "example/project",
        "commit_sha": "a" * 40,
        "project_type": "python",
        "runtime_language": "python",
        "confidence": 0.9,
    }
    values.update(overrides)
    return RepositoryAnalysis.model_validate(values)


def test_simple_pip_plan_is_finite_and_structured() -> None:
    command = "python -m pip install -r requirements.txt"
    analysis = _analysis(
        install_commands=[command],
        evidence=[
            _evidence(
                "install_command",
                command,
                EvidenceProvenance.DETERMINISTICALLY_DETECTED,
                "requirements.txt",
            )
        ],
    )

    plan = ReproductionPlanner().create_plan(analysis)

    assert [step.step_id for step in plan.steps] == ["step-001"]
    assert plan.steps[0].action_type is PlanActionType.INSTALL_DEPENDENCY
    assert plan.steps[0].command == command
    assert plan.steps[0].network_required is True
    assert plan.baseline is PlanBaseline.STRUCTURED_METADATA


def test_pyproject_entrypoint_becomes_explicit_run_step() -> None:
    install = "python -m pip install ."
    entrypoint = "fixture = fixture.cli:main"
    analysis = _analysis(
        install_commands=[install],
        entrypoints=[entrypoint],
        evidence=[
            _evidence(
                "install_command",
                install,
                EvidenceProvenance.DETERMINISTICALLY_DETECTED,
                "pyproject.toml",
            ),
            _evidence(
                "entrypoint",
                entrypoint,
                EvidenceProvenance.DETERMINISTICALLY_DETECTED,
                "pyproject.toml",
            ),
        ],
    )

    plan = ReproductionPlanner().create_plan(analysis)

    assert [step.action_type for step in plan.steps] == [
        PlanActionType.INSTALL_DEPENDENCY,
        PlanActionType.RUN_DEMO,
    ]
    assert plan.steps[1].command == "fixture"


def test_documented_readme_attempt_precedes_metadata_and_ci() -> None:
    documented = "python -m pip install -r docs-requirements.txt"
    metadata = "python -m pip install ."
    ci = "python -m pip install -r ci-requirements.txt"
    analysis = _analysis(
        install_commands=[metadata, ci, documented],
        run_commands=["python demo.py"],
        test_commands=["python -m pytest"],
        evidence=[
            _evidence(
                "install_command",
                metadata,
                EvidenceProvenance.DETERMINISTICALLY_DETECTED,
                "pyproject.toml",
            ),
            _evidence(
                "install_command",
                ci,
                EvidenceProvenance.DETERMINISTICALLY_DETECTED,
                ".github/workflows/test.yml",
            ),
            _evidence(
                "install_command",
                documented,
                EvidenceProvenance.DOCUMENTED,
                "README.md",
            ),
            _evidence(
                "run_command",
                "python demo.py",
                EvidenceProvenance.DOCUMENTED,
                "README.md",
            ),
            _evidence(
                "test_command",
                "python -m pytest",
                EvidenceProvenance.DETERMINISTICALLY_DETECTED,
                "pytest.ini",
            ),
        ],
    )

    plan = ReproductionPlanner().create_plan(analysis)

    assert [step.command for step in plan.steps[:3]] == [documented, metadata, ci]
    assert plan.baseline is PlanBaseline.OFFICIAL_DOCUMENTATION
    assert [step.action_type for step in plan.steps[-2:]] == [
        PlanActionType.RUN_DEMO,
        PlanActionType.RUN_TESTS,
    ]


def test_undocumented_project_produces_nonexecuting_selection_step() -> None:
    plan = ReproductionPlanner().create_plan(_analysis(confidence=0.3))

    assert len(plan.steps) == 1
    assert plan.steps[0].action_type is PlanActionType.VERIFY_BASIC_EXECUTION
    assert plan.steps[0].command is None
    assert plan.steps[0].risk is RiskLevel.MEDIUM
    assert plan.baseline is PlanBaseline.EXPLICIT_INFERENCE


def test_gpu_and_external_asset_requirements_remain_visible() -> None:
    asset = "https://example.com/model.ckpt"
    analysis = _analysis(
        gpu_required=True,
        external_assets=[asset],
        evidence=[
            _evidence(
                "gpu_requirement",
                "CUDA required",
                EvidenceProvenance.DOCUMENTED,
                "README.md",
            ),
            _evidence(
                "external_asset",
                asset,
                EvidenceProvenance.DOCUMENTED,
                "README.md",
            ),
        ],
    )

    plan = ReproductionPlanner().create_plan(analysis)

    assert [step.action_type for step in plan.steps] == [
        PlanActionType.PREPARE_ENVIRONMENT,
        PlanActionType.PREPARE_ASSET,
        PlanActionType.VERIFY_BASIC_EXECUTION,
    ]
    assert plan.steps[0].risk is RiskLevel.HIGH
    assert plan.steps[1].network_required is True


@pytest.mark.parametrize(
    "command",
    [
        "docker run --privileged image",
        "docker run -v /var/run/docker.sock:/var/run/docker.sock image",
        "docker system prune -af",
        "rm -rf /",
        "sudo apt-get install package",
        "cat ~/.ssh/id_rsa",
        "curl https://user:secret@example.test/archive",
        "export GITHUB_TOKEN=secret",
    ],
)
def test_unsafe_commands_are_rejected(command: str) -> None:
    analysis = _analysis(
        run_commands=[command],
        evidence=[
            _evidence(
                "run_command",
                command,
                EvidenceProvenance.LLM_INFERRED,
                "README.md",
            )
        ],
    )

    with pytest.raises(UnsafePlanError, match="prohibited"):
        ReproductionPlanner().create_plan(analysis)


def test_excessive_plan_is_rejected() -> None:
    commands = [f"python demo_{index}.py" for index in range(3)]
    analysis = _analysis(run_commands=commands)

    with pytest.raises(PlanLimitError, match="3 steps"):
        ReproductionPlanner(PlannerLimits(max_steps=2)).create_plan(analysis)


def test_timeout_sum_cannot_exceed_overall_budget() -> None:
    analysis = _analysis(
        install_commands=["python -m pip install ."],
        test_commands=["python -m pytest"],
    )
    limits = PlannerLimits(
        per_step_timeout_seconds=60,
        overall_timeout_seconds=100,
    )

    with pytest.raises(PlanLimitError, match="overall execution budget"):
        ReproductionPlanner(limits).create_plan(analysis)


def test_same_analysis_always_produces_same_order() -> None:
    analysis = _analysis(
        install_commands=["python -m pip install ."],
        run_commands=["python demo.py"],
        test_commands=["python -m pytest"],
    )
    planner = ReproductionPlanner()

    first = planner.create_plan(analysis)
    second = planner.create_plan(analysis)

    assert first == second


def test_tests_goal_excludes_demo_commands_and_requires_test_milestone() -> None:
    analysis = _analysis(
        install_commands=["python -m pip install ."],
        run_commands=["python demo.py"],
        test_commands=["python -m pytest"],
    )

    plan = ReproductionPlanner().create_plan(analysis, goal="tests")

    assert [step.action_type for step in plan.steps] == [
        PlanActionType.INSTALL_DEPENDENCY,
        PlanActionType.RUN_TESTS,
    ]
    assert plan.goal == "tests"
    assert plan.requested_goal == "tests"
    assert plan.required_milestones == ["tests"]


def test_demo_goal_without_demo_command_creates_unavailable_milestone() -> None:
    plan = ReproductionPlanner().create_plan(
        _analysis(install_commands=["python -m pip install ."]), goal="demo"
    )

    assert plan.steps[-1].action_type is PlanActionType.RUN_DEMO
    assert plan.steps[-1].command is None
    assert plan.required_milestones == ["demo"]


def test_plan_retains_command_provenance_and_source() -> None:
    command = "python -m pytest"
    analysis = _analysis(
        test_commands=[command],
        evidence=[
            _evidence(
                "test_command",
                command,
                EvidenceProvenance.DOCUMENTED,
                "README.md",
            )
        ],
    )

    plan = ReproductionPlanner().create_plan(analysis, goal="tests")

    assert plan.steps[0].provenance is PlanProvenance.DOCUMENTED
    assert plan.steps[0].source_path == "README.md"
