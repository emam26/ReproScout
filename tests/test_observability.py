from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from reproscout.observability import (
    ObservabilityError,
    ObservabilityIndex,
    build_run_observability,
)
from reproscout.state import (
    AgentAction,
    Attempt,
    Event,
    EventType,
    RunOutcome,
    SQLiteRunStore,
    Stage,
    ToolCall,
    ToolResult,
)
from reproscout.status import (
    ReproductionStatus,
    ReproductionStatusResult,
    StatusReason,
    StatusReasonCode,
)
from reproscout.verification import (
    VerificationCheck,
    VerificationCheckStatus,
    VerificationEvidence,
    VerificationLevel,
    VerificationResult,
    VerificationResultStatus,
)


def _verification() -> VerificationResult:
    return VerificationResult(
        status=VerificationResultStatus.PASSED,
        level=VerificationLevel.L2,
        contract_goal="The documented command completes.",
        checks=[
            VerificationCheck(
                check_id="check-001",
                target_id="verify-001",
                target_type="COMMAND_EXITS_SUCCESSFULLY",
                level=VerificationLevel.L2,
                status=VerificationCheckStatus.PASSED,
                reason="The command exited successfully.",
                evidence=[
                    VerificationEvidence(
                        evidence_id="evidence-001",
                        source="execution",
                        detail="exit_code=0",
                    )
                ],
            )
        ],
        summary="The command passed.",
    )


def _status() -> ReproductionStatusResult:
    return ReproductionStatusResult(
        status=ReproductionStatus.REPRODUCED,
        reasons=[
            StatusReason(
                reason_id="reason-001",
                code=StatusReasonCode.OBJECTIVE_VERIFICATION_PASSED,
                detail="Objective verification passed.",
            )
        ],
        verification_status=VerificationResultStatus.PASSED,
        verification_level=VerificationLevel.L2,
        workflow_succeeded=True,
    )


def test_observability_projects_persisted_events_and_results(tmp_path: Path) -> None:
    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        run = store.create_run(context={"repository": "fixture/project"})
        store.transition(run.run_id, Stage.ANALYZE)
        store.transition(run.run_id, Stage.PLAN)
        store.record_attempt(
            run.run_id,
            Attempt.create("plan_execution", Stage.EXECUTE),
        )
        store.record_attempt(
            run.run_id,
            Attempt.create("repair", Stage.DEBUG),
        )
        store.record_agent_action(
            run.run_id,
            AgentAction.create(
                "diagnosis_result",
                {
                    "status": "LLM",
                    "usage": {"input_tokens": 17, "output_tokens": 11},
                    "diagnosis": {"failure_class": "MISSING_PACKAGE"},
                },
            ),
        )
        call = ToolCall.create(
            "docker_execute",
            {"network_mode": "none", "command": "python demo.py"},
        )
        store.record_tool_call(run.run_id, call)
        store.record_tool_result(
            run.run_id,
            ToolResult.create(
                call.call_id,
                succeeded=False,
                payload={
                    "container_id": "container-123",
                    "network_mode": "none",
                    "failure_kind": "COMMAND_NONZERO",
                    "stderr": "api_key=should-never-appear",
                },
            ),
        )
        store.transition(run.run_id, Stage.SETUP)
        store.transition(run.run_id, Stage.EXECUTE)
        store.transition(run.run_id, Stage.VERIFY)
        store.transition(run.run_id, Stage.REPORT)
        store.finish(run.run_id, RunOutcome.SUCCEEDED)
        observation = build_run_observability(
            store.list_events(run.run_id),
            verification=_verification(),
            final_status=_status(),
        )

    assert observation.metrics.run_id == run.run_id
    assert observation.metrics.duration_seconds >= 0
    assert observation.metrics.attempt_count == 2
    assert observation.metrics.repair_count == 1
    assert observation.metrics.llm_calls == 1
    assert observation.metrics.input_tokens == 17
    assert observation.metrics.output_tokens == 11
    assert observation.metrics.token_usage_complete is True
    assert observation.metrics.failure_categories == {
        "COMMAND_NONZERO": 1,
        "MISSING_PACKAGE": 1,
    }
    assert observation.metrics.verification_level is VerificationLevel.L2
    assert observation.metrics.final_status is ReproductionStatus.REPRODUCED
    assert observation.metrics.docker_container_ids == ["container-123"]
    assert observation.metrics.network_modes == ["none"]
    assert [entry.sequence for entry in observation.timeline] == list(
        range(1, len(observation.timeline) + 1)
    )
    assert observation.query_timeline(stage=Stage.EXECUTE)
    assert "should-never-appear" not in json.dumps(observation.model_dump(mode="json"))


def test_observability_index_supports_evaluation_queries() -> None:
    event = Event(
        run_id="run-001",
        sequence=1,
        event_type=EventType.RUN_CREATED,
        stage=Stage.INTAKE,
        timestamp=datetime.now(UTC),
        payload={"context": {}},
    )
    observation = build_run_observability(
        [event],
        final_status=_status(),
    )
    index = ObservabilityIndex()
    index.add(observation)

    assert index.get("run-001") is observation
    assert index.query(final_status=ReproductionStatus.REPRODUCED) == [observation]
    assert index.query(failure_category="missing") == []


def test_observability_rejects_mixed_or_non_monotonic_streams() -> None:
    first = datetime(2026, 1, 1, tzinfo=UTC)
    events = [
        Event(
            run_id="run-001",
            sequence=1,
            event_type=EventType.RUN_CREATED,
            stage=Stage.INTAKE,
            timestamp=first,
            payload={"context": {}},
        ),
        Event(
            run_id="run-001",
            sequence=2,
            event_type=EventType.RUN_FINISHED,
            stage=Stage.DONE,
            timestamp=first - timedelta(seconds=1),
            payload={"outcome": "SUCCEEDED"},
        ),
    ]

    try:
        build_run_observability(events)
    except ObservabilityError as exc:
        assert "timestamps" in str(exc)
    else:
        raise AssertionError("Expected non-monotonic timestamps to be rejected")
