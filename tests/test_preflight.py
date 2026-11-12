from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from reproscout.analysis import RepositoryAnalysis, ResolvedEnvironment
from reproscout.diagnostics import EnvironmentFingerprint, EnvironmentPackage
from reproscout.diagnostics.models import RequirementRecord
from reproscout.execution import (
    ExecutionFailureKind,
    ExecutionRunResult,
    RunArtifactPaths,
    StepExecutionResult,
)
from reproscout.planning import PlanActionType
from reproscout.preflight import (
    PreflightArtifactWriter,
    PreflightInspection,
    PreflightPlanner,
    PreflightStatus,
    evaluate_preflight,
)
from reproscout.preflight.detector import inspect_environment
from reproscout.preflight.evaluator import _python_constraints_are_observed
from reproscout.preflight.models import (
    PreflightRequirement,
    RequirementKind,
    RequirementState,
)
from reproscout.repo.manifest import RepositoryManifest


def _analysis(**updates: object) -> RepositoryAnalysis:
    values: dict[str, object] = {
        "repository": "example/project",
        "repository_url": "https://github.com/example/project",
        "commit_sha": "a" * 40,
        "project_type": "python",
        "runtime_language": "python",
        "confidence": 0.9,
        "resolved_environment": ResolvedEnvironment(),
    }
    values.update(updates)
    return RepositoryAnalysis.model_validate(values)


def _inspection(
    *,
    dependency_sources: list[str] | None = None,
    system_tools: list[str] | None = None,
    incompatibilities: list[str] | None = None,
    missing_prerequisites: list[str] | None = None,
    unsafe_findings: list[str] | None = None,
) -> PreflightInspection:
    sources = dependency_sources or []
    requirements = [
        PreflightRequirement(
            kind=RequirementKind.PYTHON,
            name="python",
            expected="supported Python",
            state=RequirementState.UNVERIFIED,
        )
    ]
    if sources:
        requirements.append(
            PreflightRequirement(
                kind=RequirementKind.PACKAGE,
                name="requests",
                expected=">=2",
                state=RequirementState.UNVERIFIED,
                source_paths=sources,
            )
        )
    for tool in system_tools or []:
        requirements.append(
            PreflightRequirement(
                kind=RequirementKind.SYSTEM_TOOL,
                name=tool,
                expected="available on PATH",
                state=RequirementState.UNVERIFIED,
            )
        )
    return PreflightInspection(
        dependency_sources=sources,
        system_tools=system_tools or [],
        package_requirements=(
            [
                RequirementRecord(
                    original="requests>=2",
                    name="requests",
                    canonical_name="requests",
                    specifier=">=2",
                )
            ]
            if sources
            else []
        ),
        requirements=requirements,
        incompatibilities=incompatibilities or [],
        missing_prerequisites=missing_prerequisites or [],
        unsafe_findings=unsafe_findings or [],
    )


def _environment() -> EnvironmentFingerprint:
    return EnvironmentFingerprint(
        repository_commit_sha="a" * 40,
        container_image="python:3.11-slim",
        os_name="Linux",
        os_release="fixture",
        architecture="x86_64",
        python_version="3.11.9",
        pip_version="25.0",
        installed_packages=[EnvironmentPackage(name="pip", version="25.0")],
        cpu_allocation=1,
        memory_limit="512m",
        gpu_visible=False,
        network_mode="bridge",
    )


def _execution(plan, *, failed_step: str | None = None) -> ExecutionRunResult:
    now = datetime.now(UTC)
    steps: list[StepExecutionResult] = []
    for plan_step in plan.steps:
        failed = plan_step.step_id == failed_step
        steps.append(
            StepExecutionResult(
                step_id=plan_step.step_id,
                attempt_number=1,
                action_type=plan_step.action_type.value,
                command=plan_step.command,
                working_directory=".",
                started_at=now,
                finished_at=now,
                duration=0.01,
                stdout="",
                stderr="dependency resolution failed" if failed else "",
                exit_code=1 if failed else 0,
                timed_out=False,
                failure_kind=(
                    ExecutionFailureKind.DEPENDENCY_INSTALLATION_FAILURE
                    if failed
                    else None
                ),
            )
        )
    return ExecutionRunResult(
        run_id="fixture-run",
        workflow_succeeded=failed_step is None,
        failure_kind=(
            ExecutionFailureKind.DEPENDENCY_INSTALLATION_FAILURE
            if failed_step is not None
            else None
        ),
        failed_step_id=failed_step,
        steps=steps,
        started_at=now,
        finished_at=now,
        artifacts=RunArtifactPaths(
            run_directory=".",
            commands_jsonl="commands.jsonl",
            events_jsonl="events.jsonl",
            setup_log="setup.log",
            execution_log="execution.log",
            patches_diff="patches.diff",
            workspace="workspace",
        ),
        environment=_environment(),
    )


def test_working_preflight_is_ready_without_target_workflows() -> None:
    inspection = _inspection(dependency_sources=["requirements.txt"])
    plan = PreflightPlanner().create_plan(
        _analysis(
            run_commands=["python train.py"],
            test_commands=["python -m pytest"],
        ),
        inspection,
    )

    assert all(
        step.action_type not in {PlanActionType.RUN_TESTS, PlanActionType.RUN_DEMO}
        for step in plan.steps
    )
    assert all("train.py" not in (step.command or "") for step in plan.steps)
    assessment = evaluate_preflight(inspection, plan, _execution(plan))

    assert assessment.status is PreflightStatus.READY
    assert assessment.verified_environment is True
    assert all(item.outcome == "PASSED" for item in assessment.commands)


def test_incompatible_environment_is_not_executed() -> None:
    inspection = _inspection(
        incompatibilities=["Python 3.13 is outside the supported runtime envelope."]
    )
    plan = PreflightPlanner().create_plan(_analysis(), inspection)
    assessment = evaluate_preflight(inspection, plan, None)

    assert assessment.status is PreflightStatus.INCOMPATIBLE
    assert all(item.command is None for item in assessment.commands)


def test_missing_dependency_installation_is_distinguished_from_readiness() -> None:
    inspection = _inspection(dependency_sources=["requirements.txt"])
    plan = PreflightPlanner().create_plan(_analysis(), inspection)
    failed_step = next(
        step.step_id
        for step in plan.steps
        if step.action_type is PlanActionType.INSTALL_DEPENDENCY
    )

    assessment = evaluate_preflight(
        inspection,
        plan,
        _execution(plan, failed_step=failed_step),
    )

    assert assessment.status is PreflightStatus.INSTALLATION_FAILED
    assert assessment.verified_environment is False


def test_missing_system_tool_is_distinguished_from_installation_failure() -> None:
    inspection = _inspection(system_tools=["ffmpeg"])
    plan = PreflightPlanner().create_plan(_analysis(), inspection)
    failed_step = next(
        step.step_id for step in plan.steps if step.command == "command -v ffmpeg"
    )

    assessment = evaluate_preflight(
        inspection,
        plan,
        _execution(plan, failed_step=failed_step),
    )

    assert assessment.status is PreflightStatus.MISSING_PREREQUISITES


def test_unsafe_instruction_stops_before_any_command() -> None:
    inspection = _inspection(unsafe_findings=["README.md:10: prohibited command"])
    plan = PreflightPlanner().create_plan(_analysis(), inspection)
    assessment = evaluate_preflight(inspection, plan, None)

    assert assessment.status is PreflightStatus.UNSAFE
    assert assessment.commands[0].command is None


def test_incomplete_environment_is_not_called_ready_without_dependency_source() -> None:
    inspection = _inspection()
    plan = PreflightPlanner().create_plan(_analysis(), inspection)
    assessment = evaluate_preflight(inspection, plan, _execution(plan))

    assert assessment.status is PreflightStatus.INCOMPLETE
    assert assessment.verified_environment is False


def test_sandbox_initialization_failure_is_unverified_not_partial() -> None:
    inspection = _inspection(dependency_sources=["requirements.txt"])
    plan = PreflightPlanner().create_plan(_analysis(), inspection)
    base_execution = _execution(plan)
    execution = base_execution.model_copy(
        update={
            "workflow_succeeded": False,
            "failure_kind": ExecutionFailureKind.SANDBOX_FAILURE,
            "steps": [
                step.model_copy(
                    update={
                        "exit_code": None,
                        "stderr": "Docker sandbox initialization failed",
                        "failure_kind": ExecutionFailureKind.SANDBOX_FAILURE,
                    }
                )
                for step in base_execution.steps
            ],
        }
    )

    assessment = evaluate_preflight(inspection, plan, execution)

    assert assessment.status is PreflightStatus.UNVERIFIED
    assert assessment.verified_environment is False


def test_sandbox_failure_during_installation_is_not_installation_failure() -> None:
    inspection = _inspection(dependency_sources=["requirements.txt"])
    plan = PreflightPlanner().create_plan(_analysis(), inspection)
    failed_step = next(
        step
        for step in plan.steps
        if step.action_type is PlanActionType.INSTALL_DEPENDENCY
    )
    base_execution = _execution(plan, failed_step=failed_step.step_id)
    execution = base_execution.model_copy(
        update={
            "failure_kind": ExecutionFailureKind.SANDBOX_FAILURE,
            "steps": [
                step.model_copy(
                    update=(
                        {
                            "failure_kind": ExecutionFailureKind.SANDBOX_FAILURE,
                            "exit_code": None,
                            "stderr": "Docker sandbox initialization failed",
                        }
                        if step.step_id == failed_step.step_id
                        else {}
                    ),
                )
                for step in base_execution.steps
            ],
        }
    )

    assessment = evaluate_preflight(inspection, plan, execution)

    assert assessment.status is PreflightStatus.UNVERIFIED
    failed_command = next(
        item for item in assessment.commands if item.step_id == failed_step.step_id
    )
    assert failed_command.exit_code is None
    assert failed_command.outcome == "FAILED"


def test_python_constraints_use_pep_440_specifiers() -> None:
    assert _python_constraints_are_observed([">=3.10,<3.12"], "3.11.9")
    assert _python_constraints_are_observed(["~=3.11"], "3.12.2")
    assert not _python_constraints_are_observed(["<3.11"], "3.11.0")


def test_detector_collects_package_tools_and_unsafe_instructions(
    tmp_path: Path,
) -> None:
    (tmp_path / "README.md").write_text(
        "Install ffmpeg first.\n\n```sh\nsudo apt-get install ffmpeg\nrm -rf /\n```\n",
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text("requests>=2\n", encoding="utf-8")
    manifest = RepositoryManifest(
        repository_url="https://github.com/example/project",
        normalized_url="https://github.com/example/project",
        owner="example",
        repository_name="project",
        commit_sha="a" * 40,
        workspace_path=tmp_path,
        documentation_files=["README.md"],
        dependency_files=["requirements.txt"],
    )
    analysis = RepositoryAnalysis(
        repository="example/project",
        commit_sha="a" * 40,
        python_version_hints=[],
        environment_variables=[],
        external_assets=[],
    )

    inspection = inspect_environment(manifest, analysis)

    assert inspection.package_requirements[0].name == "requests"
    assert inspection.system_tools == ["ffmpeg"]
    assert inspection.unsafe_findings
    assert "sudo apt-get install ffmpeg" in inspection.unsafe_findings[0]
    assert "not executed" in inspection.unsafe_findings[0]


def test_detector_preserves_optional_pyproject_requirements(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        "name = 'fixture'\n"
        "version = '0.1.0'\n"
        "dependencies = ['requests>=2']\n"
        "[project.optional-dependencies]\n"
        "gpu = ['torch>=2']\n",
        encoding="utf-8",
    )
    manifest = RepositoryManifest(
        repository_url="https://github.com/example/project",
        normalized_url="https://github.com/example/project",
        owner="example",
        repository_name="project",
        commit_sha="a" * 40,
        workspace_path=tmp_path,
        dependency_files=["pyproject.toml"],
    )

    inspection = inspect_environment(manifest, _analysis())

    assert [item.name for item in inspection.package_requirements] == ["requests"]
    assert [item.name for item in inspection.optional_package_requirements] == [
        "torch"
    ]
    assert any(item.optional for item in inspection.requirements)


def test_preflight_artifacts_include_commit_commands_and_scope(tmp_path: Path) -> None:
    inspection = _inspection(dependency_sources=["requirements.txt"])
    plan = PreflightPlanner().create_plan(_analysis(), inspection)
    assessment = evaluate_preflight(inspection, plan, _execution(plan))
    manifest = RepositoryManifest(
        repository_url="https://github.com/example/project",
        normalized_url="https://github.com/example/project",
        owner="example",
        repository_name="project",
        commit_sha="a" * 40,
        workspace_path=tmp_path,
    )

    paths = PreflightArtifactWriter(tmp_path / "run").write(
        run_id="fixture-run",
        manifest=manifest,
        plan=plan,
        inspection=inspection,
        assessment=assessment,
    )

    assert paths.environment_report.is_file()
    assert paths.environment_evidence.is_file()
    assert paths.agent_handoff.is_file()
    evidence = json.loads(paths.environment_evidence.read_text(encoding="utf-8"))
    assert evidence["run_id"] == "fixture-run"
    handoff = paths.agent_handoff.read_text(encoding="utf-8")
    assert "fixture-run" in handoff
    assert "a" * 40 in handoff
    assert "training" in handoff
    assert "python -m pip check" in handoff


def test_failed_preflight_artifacts_do_not_claim_a_revision_or_readiness(
    tmp_path: Path,
) -> None:
    paths = PreflightArtifactWriter(tmp_path / "failed").write_failure(
        run_id="failed-run",
        repository="https://github.com/example/project",
        error="Git failed: TOKEN=secret",
    )

    evidence = json.loads(paths.environment_evidence.read_text(encoding="utf-8"))
    report = paths.environment_report.read_text(encoding="utf-8")
    handoff = paths.agent_handoff.read_text(encoding="utf-8")
    assert evidence["commit_sha"] is None
    assert evidence["verified_environment"] is False
    assert evidence["status"] == PreflightStatus.UNVERIFIED.value
    assert "secret" not in json.dumps(evidence)
    assert "secret" not in report
    assert "secret" not in handoff


def test_failure_artifacts_keep_sensitive_diagnostics_valid_and_redacted(
    tmp_path: Path,
) -> None:
    paths = PreflightArtifactWriter(tmp_path / "sensitive-failure").write_failure(
        run_id="sensitive-run",
        repository="https://github.com/example/project",
        error=(
            'Docker failed\nTOKEN="secret"\tpath="C:\\workspace"\r'
            "Authorization: Bearer bearer-secret\x1b[31m"
        ),
    )

    evidence_text = paths.environment_evidence.read_text(encoding="utf-8")
    evidence = json.loads(evidence_text)

    assert evidence["status"] == PreflightStatus.UNVERIFIED.value
    assert evidence["verified_environment"] is False
    assert evidence["failure"]["detail"]
    assert "secret" not in evidence_text
    assert "bearer-secret" not in evidence_text
    assert "Docker failed" in evidence["failure"]["detail"]
