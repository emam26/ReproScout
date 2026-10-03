from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from reproscout.diagnostics import (
    EnvironmentFingerprint,
    VerificationContract,
    VerificationTarget,
    VerificationTargetType,
)
from reproscout.execution import (
    ExecutionFailureKind,
    ExecutionRunResult,
    RunArtifactPaths,
    StepExecutionResult,
)
from reproscout.verification import (
    ObjectiveVerificationEngine,
    VerificationCheckStatus,
    VerificationLevel,
    VerificationResultStatus,
)


def _step(
    step_id: str,
    command: str,
    *,
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
    failure_kind: ExecutionFailureKind | None = None,
) -> StepExecutionResult:
    now = datetime.now(UTC)
    return StepExecutionResult(
        step_id=step_id,
        attempt_number=1,
        action_type="RUN_DEMO",
        command=command,
        working_directory=".",
        started_at=now,
        finished_at=now,
        duration=0.01,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        timed_out=False,
        failure_kind=failure_kind,
    )


def _execution(
    steps: list[StepExecutionResult], *, succeeded: bool = True
) -> ExecutionRunResult:
    now = datetime.now(UTC)
    return ExecutionRunResult(
        run_id="verify-run",
        workflow_succeeded=succeeded,
        failure_kind=None if succeeded else ExecutionFailureKind.COMMAND_NONZERO,
        steps=steps,
        started_at=now,
        finished_at=now,
        artifacts=RunArtifactPaths(
            run_directory="runs/verify-run",
            commands_jsonl="runs/verify-run/commands.jsonl",
            events_jsonl="runs/verify-run/events.jsonl",
            setup_log="runs/verify-run/logs/setup.log",
            execution_log="runs/verify-run/logs/execution.log",
            patches_diff="runs/verify-run/patches.diff",
            workspace="runs/verify-run/workspace",
        ),
    )


def _environment() -> EnvironmentFingerprint:
    return EnvironmentFingerprint(
        repository_commit_sha="a" * 40,
        container_image="python:3.11-slim",
        os_name="Linux",
        os_release="fixture",
        architecture="x86_64",
        python_version="3.11.0",
        installed_packages=[],
        cpu_allocation=1,
        memory_limit="512m",
        gpu_visible=False,
        environment_variable_names=[],
        network_mode="none",
    )


def test_verifier_passes_environment_command_test_and_file_facts(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "result.txt"
    artifact.write_text("verified\n", encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    contract = VerificationContract(
        goal="Verify the fixture.",
        targets=[
            VerificationTarget(
                target_id="verify-001",
                target_type=VerificationTargetType.ENVIRONMENT_SETUP,
                description="Environment exists.",
            ),
            VerificationTarget(
                target_id="verify-002",
                target_type=VerificationTargetType.INSTALLATION_SUCCEEDS,
                description="Install succeeds.",
            ),
            VerificationTarget(
                target_id="verify-003",
                target_type=VerificationTargetType.COMMAND_EXITS_SUCCESSFULLY,
                description="Demo succeeds.",
                command="python app.py",
            ),
            VerificationTarget(
                target_id="verify-004",
                target_type=VerificationTargetType.TESTS_EXECUTE,
                description="Tests pass.",
                command="python -m pytest",
            ),
            VerificationTarget(
                target_id="verify-005",
                target_type=VerificationTargetType.ARTIFACT_EXISTS,
                description="Result exists.",
                artifact_path="result.txt",
                artifact_type="file",
                artifact_size_bytes=len(artifact.read_bytes()),
                artifact_sha256=digest,
            ),
            VerificationTarget(
                target_id="verify-006",
                target_type=VerificationTargetType.OUTPUT_CONDITION,
                description="Output contains the marker.",
                command="python app.py",
                output_pattern=r"fixture output",
            ),
        ],
    )
    result = ObjectiveVerificationEngine().verify(
        contract,
        execution=_execution(
            [
                _step("step-001", "python -m pip install ."),
                _step("step-002", "python app.py", stdout="fixture output\n"),
                _step("step-003", "python -m pytest", stdout="2 passed\n"),
            ]
        ),
        workspace=tmp_path,
        environment=_environment(),
    )

    assert result.status is VerificationResultStatus.PASSED
    assert result.level is VerificationLevel.L3
    assert all(
        check.status is VerificationCheckStatus.PASSED for check in result.checks
    )


def test_verifier_distinguishes_execution_failure_from_verification_failure(
    tmp_path: Path,
) -> None:
    execution_contract = VerificationContract(
        goal="Run command.",
        targets=[
            VerificationTarget(
                target_id="verify-001",
                target_type=VerificationTargetType.COMMAND_EXITS_SUCCESSFULLY,
                description="Command succeeds.",
                command="python app.py",
            )
        ],
    )
    execution_failed = ObjectiveVerificationEngine().verify(
        execution_contract,
        execution=_execution(
            [
                _step(
                    "step-001",
                    "python app.py",
                    stderr="boom",
                    exit_code=1,
                    failure_kind=ExecutionFailureKind.COMMAND_NONZERO,
                )
            ],
            succeeded=False,
        ),
    )
    assert execution_failed.status is VerificationResultStatus.EXECUTION_FAILED
    assert execution_failed.checks[0].status is VerificationCheckStatus.EXECUTION_FAILED

    artifact_contract = VerificationContract(
        goal="Check artifact.",
        targets=[
            VerificationTarget(
                target_id="verify-001",
                target_type=VerificationTargetType.ARTIFACT_EXISTS,
                description="Artifact exists.",
                artifact_path="missing.txt",
            )
        ],
    )
    verification_failed = ObjectiveVerificationEngine().verify(
        artifact_contract,
        workspace=tmp_path,
    )
    assert verification_failed.status is VerificationResultStatus.FAILED
    assert verification_failed.checks[0].status is VerificationCheckStatus.FAILED


def test_verifier_distinguishes_unavailable_and_unspecified_and_blocks_escape(
    tmp_path: Path,
) -> None:
    command_contract = VerificationContract(
        goal="Unavailable command.",
        targets=[
            VerificationTarget(
                target_id="verify-001",
                target_type=VerificationTargetType.COMMAND_EXITS_SUCCESSFULLY,
                description="Command evidence is required.",
                command="python app.py",
            )
        ],
    )
    unavailable = ObjectiveVerificationEngine().verify(command_contract)
    assert unavailable.status is VerificationResultStatus.UNAVAILABLE

    optional_contract = VerificationContract(
        goal="Optional evidence.",
        targets=[
            VerificationTarget(
                target_id="verify-001",
                target_type=VerificationTargetType.COMMAND_EXITS_SUCCESSFULLY,
                description="Optional command.",
                command="python app.py",
                required=False,
            )
        ],
    )
    unspecified = ObjectiveVerificationEngine().verify(optional_contract)
    assert unspecified.status is VerificationResultStatus.UNSPECIFIED

    escape_contract = VerificationContract(
        goal="Unsafe path.",
        targets=[
            VerificationTarget(
                target_id="verify-001",
                target_type=VerificationTargetType.ARTIFACT_EXISTS,
                description="No escape.",
                artifact_path="../outside.txt",
            )
        ],
    )
    escaped = ObjectiveVerificationEngine().verify(escape_contract, workspace=tmp_path)
    assert escaped.status is VerificationResultStatus.UNAVAILABLE
