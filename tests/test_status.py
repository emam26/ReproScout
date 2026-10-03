from __future__ import annotations

from datetime import UTC, datetime

from reproscout.diagnostics import (
    VerificationContract,
    VerificationTarget,
    VerificationTargetType,
)
from reproscout.execution import (
    ExecutionRunResult,
    RunArtifactPaths,
    StepExecutionResult,
)
from reproscout.status import (
    ReproductionStatus,
    StatusReasonCode,
    compute_reproduction_status,
)
from reproscout.verification import ObjectiveVerificationEngine


def _execution(*, exit_code: int = 0) -> ExecutionRunResult:
    now = datetime.now(UTC)
    return ExecutionRunResult(
        run_id="status-run",
        workflow_succeeded=exit_code == 0,
        steps=[
            StepExecutionResult(
                step_id="step-001",
                attempt_number=1,
                action_type="RUN_DEMO",
                command="python app.py",
                working_directory=".",
                started_at=now,
                finished_at=now,
                duration=0.1,
                stdout="ok\n" if exit_code == 0 else "",
                stderr="" if exit_code == 0 else "failure\n",
                exit_code=exit_code,
                timed_out=False,
            )
        ],
        started_at=now,
        finished_at=now,
        artifacts=RunArtifactPaths(
            run_directory="runs/status-run",
            commands_jsonl="runs/status-run/commands.jsonl",
            events_jsonl="runs/status-run/events.jsonl",
            setup_log="runs/status-run/logs/setup.log",
            execution_log="runs/status-run/logs/execution.log",
            patches_diff="runs/status-run/patches.diff",
            workspace="runs/status-run/workspace",
        ),
    )


def _command_contract() -> VerificationContract:
    return VerificationContract(
        goal="Run the application.",
        targets=[
            VerificationTarget(
                target_id="verify-001",
                target_type=VerificationTargetType.COMMAND_EXITS_SUCCESSFULLY,
                description="The application exits successfully.",
                command="python app.py",
            )
        ],
    )


def _verification(*, exit_code: int = 0):
    execution = _execution(exit_code=exit_code)
    return ObjectiveVerificationEngine().verify(
        _command_contract(),
        execution=execution,
    )


def test_status_rules_distinguish_reproduction_from_workflow_outcome() -> None:
    passed = _verification()
    reproduced = compute_reproduction_status(passed, workflow_succeeded=True)
    assert reproduced.status is ReproductionStatus.REPRODUCED

    not_confirmed = compute_reproduction_status(passed, workflow_succeeded=None)
    assert not_confirmed.status is ReproductionStatus.PARTIAL
    assert not_confirmed.reasons[0].code is StatusReasonCode.WORKFLOW_NOT_CONFIRMED

    failed_execution = compute_reproduction_status(
        _verification(exit_code=1),
        workflow_succeeded=False,
    )
    assert failed_execution.status is ReproductionStatus.FAILED
    assert failed_execution.reasons[0].code is StatusReasonCode.EXECUTION_FAILED


def test_status_rules_cover_blocked_failed_unsafe_and_clean_room_cases() -> None:
    assert (
        compute_reproduction_status(None, workflow_succeeded=False).status
        is ReproductionStatus.BLOCKED
    )
    assert (
        compute_reproduction_status(
            _verification(),
            workflow_succeeded=True,
            clean_room_required=True,
        ).status
        is ReproductionStatus.PARTIAL
    )
    assert (
        compute_reproduction_status(
            _verification(),
            workflow_succeeded=True,
            clean_room_required=True,
            clean_room_verified=False,
        ).status
        is ReproductionStatus.PARTIAL
    )
    assert (
        compute_reproduction_status(
            _verification(),
            workflow_succeeded=True,
            clean_room_required=True,
            clean_room_verified=True,
        ).status
        is ReproductionStatus.REPRODUCED
    )

    unsafe = compute_reproduction_status(
        _verification(),
        workflow_succeeded=True,
        unsafe_findings=[
            "Docker socket access was requested.",
            "Host path escape was detected.",
        ],
    )
    assert unsafe.status is ReproductionStatus.UNSAFE
    assert [reason.reason_id for reason in unsafe.reasons] == [
        "reason-001",
        "reason-002",
    ]


def test_status_result_can_report_verification_failure_and_unavailability() -> None:
    failed_contract = VerificationContract(
        goal="Expected marker.",
        targets=[
            VerificationTarget(
                target_id="verify-001",
                target_type=VerificationTargetType.OUTPUT_CONDITION,
                description="Marker is present.",
                command="python app.py",
                output_pattern="missing-marker",
            )
        ],
    )
    failed = ObjectiveVerificationEngine().verify(
        failed_contract,
        execution=_execution(),
    )
    result = compute_reproduction_status(failed, workflow_succeeded=True)
    assert result.status is ReproductionStatus.FAILED
    assert result.reasons[0].code is StatusReasonCode.OBJECTIVE_VERIFICATION_FAILED

    unavailable = ObjectiveVerificationEngine().verify(_command_contract())
    blocked = compute_reproduction_status(unavailable, workflow_succeeded=False)
    assert blocked.status is ReproductionStatus.BLOCKED
    assert blocked.reasons[0].code is StatusReasonCode.VERIFICATION_UNAVAILABLE
