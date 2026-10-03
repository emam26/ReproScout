from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC
from pathlib import Path

import pytest

from reproscout.state import (
    LEGAL_TRANSITIONS,
    AgentAction,
    Attempt,
    EventType,
    InvalidTransitionError,
    PersistenceError,
    RunNotFoundError,
    RunOutcome,
    RunState,
    SQLiteRunStore,
    Stage,
    ToolCall,
    ToolResult,
)
from reproscout.state.models import format_timestamp, utc_now


def _advance(
    store: SQLiteRunStore,
    run_id: str,
    stages: Iterable[Stage],
) -> RunState:
    state = store.get_run(run_id)
    for stage in stages:
        state = store.transition(run_id, stage)
    return state


def test_stage_graph_is_exact() -> None:
    assert list(Stage) == [
        Stage.INTAKE,
        Stage.ANALYZE,
        Stage.PLAN,
        Stage.SETUP,
        Stage.EXECUTE,
        Stage.DEBUG,
        Stage.VERIFY,
        Stage.REPORT,
        Stage.DONE,
    ]
    assert LEGAL_TRANSITIONS == {
        Stage.INTAKE: frozenset({Stage.ANALYZE}),
        Stage.ANALYZE: frozenset({Stage.PLAN}),
        Stage.PLAN: frozenset({Stage.SETUP}),
        Stage.SETUP: frozenset({Stage.EXECUTE}),
        Stage.EXECUTE: frozenset({Stage.DEBUG, Stage.VERIFY}),
        Stage.DEBUG: frozenset({Stage.EXECUTE, Stage.VERIFY}),
        Stage.VERIFY: frozenset({Stage.DEBUG, Stage.REPORT}),
        Stage.REPORT: frozenset({Stage.DONE}),
        Stage.DONE: frozenset(),
    }


def test_run_creation_persists_intake_snapshot_and_first_event(tmp_path: Path) -> None:
    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        state = store.create_run(
            run_id="run-create",
            context={"repository": "example/project", "revision": "abc123"},
        )

        event = store.list_events(state.run_id)[0]

    assert state.stage is Stage.INTAKE
    assert state.outcome is None
    assert state.context == {"repository": "example/project", "revision": "abc123"}
    assert event.sequence == 1
    assert event.event_type is EventType.RUN_CREATED
    assert event.stage is Stage.INTAKE
    assert event.payload == {"context": dict(state.context)}
    assert state.created_at.tzinfo is UTC
    assert format_timestamp(state.created_at).endswith("Z")


def test_transition_updates_snapshot_and_records_audit_event(tmp_path: Path) -> None:
    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        run = store.create_run()
        state = store.transition(run.run_id, Stage.ANALYZE)
        events = store.list_events(run.run_id)

    assert state.stage is Stage.ANALYZE
    assert state.outcome is None
    assert events[-1].event_type is EventType.STAGE_TRANSITIONED
    assert events[-1].stage is Stage.ANALYZE
    assert events[-1].payload == {
        "from_stage": Stage.INTAKE.value,
        "to_stage": Stage.ANALYZE.value,
    }


def test_debug_can_proceed_directly_to_verify(tmp_path: Path) -> None:
    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        run = store.create_run()
        state = _advance(
            store,
            run.run_id,
            [
                Stage.ANALYZE,
                Stage.PLAN,
                Stage.SETUP,
                Stage.EXECUTE,
                Stage.DEBUG,
                Stage.VERIFY,
            ],
        )
        events = store.list_events(run.run_id)

    assert state.stage is Stage.VERIFY
    assert events[-1].payload == {
        "from_stage": Stage.DEBUG.value,
        "to_stage": Stage.VERIFY.value,
    }


def test_invalid_transition_is_typed_and_rolls_back_snapshot_and_events(
    tmp_path: Path,
) -> None:
    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        run = store.create_run()
        before = store.list_events(run.run_id)

        with pytest.raises(
            InvalidTransitionError,
            match="INTAKE to PLAN",
        ) as exc_info:
            store.transition(run.run_id, Stage.PLAN)

        after = store.list_events(run.run_id)
        state = store.get_run(run.run_id)

    assert exc_info.value.current_stage is Stage.INTAKE
    assert exc_info.value.requested_stage is Stage.PLAN
    assert state.stage is Stage.INTAKE
    assert after == before


def test_transition_rolls_back_if_event_insert_fails(tmp_path: Path) -> None:
    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        run = store.create_run()
        before = store.list_events(run.run_id)
        store._connection.executescript(
            """
            CREATE TRIGGER reject_transition_event
            BEFORE INSERT ON events
            WHEN NEW.event_type = 'STAGE_TRANSITIONED'
            BEGIN
                SELECT RAISE(ABORT, 'forced transition event failure');
            END;
            """
        )

        with pytest.raises(PersistenceError, match="rolled back"):
            store.transition(run.run_id, Stage.ANALYZE)

        state = store.get_run(run.run_id)
        after = store.list_events(run.run_id)

    assert state.stage is Stage.INTAKE
    assert after == before


def test_finish_requires_report_and_outcome_is_terminal_only(tmp_path: Path) -> None:
    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        run = store.create_run()

        with pytest.raises(InvalidTransitionError, match="INTAKE to DONE"):
            store.finish(run.run_id, RunOutcome.SUCCEEDED)

        finished = store.finish(
            _advance(
                store,
                run.run_id,
                [
                    Stage.ANALYZE,
                    Stage.PLAN,
                    Stage.SETUP,
                    Stage.EXECUTE,
                    Stage.VERIFY,
                    Stage.REPORT,
                ],
            ).run_id,
            RunOutcome.SUCCEEDED,
        )

    assert finished.stage is Stage.DONE
    assert finished.outcome is RunOutcome.SUCCEEDED
    with pytest.raises(ValueError, match="Only DONE"):
        RunState(
            run_id="not-finished",
            stage=Stage.REPORT,
            outcome=RunOutcome.FAILED,
            context={},
            created_at=utc_now(),
            updated_at=utc_now(),
        )


def test_done_is_terminal_and_finish_appends_final_event(tmp_path: Path) -> None:
    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        run = store.create_run()
        _advance(
            store,
            run.run_id,
            [
                Stage.ANALYZE,
                Stage.PLAN,
                Stage.SETUP,
                Stage.EXECUTE,
                Stage.VERIFY,
                Stage.REPORT,
            ],
        )
        store.finish(run.run_id, RunOutcome.CANCELLED)

        with pytest.raises(InvalidTransitionError, match="DONE to REPORT"):
            store.transition(run.run_id, Stage.REPORT)
        events = store.list_events(run.run_id)

    assert events[-1].event_type is EventType.RUN_FINISHED
    assert events[-1].stage is Stage.DONE
    assert events[-1].payload == {"outcome": RunOutcome.CANCELLED.value}


def test_event_sequences_are_monotonic_and_retrieval_is_ordered(tmp_path: Path) -> None:
    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        run = store.create_run()
        store.append_event(run.run_id, EventType.AGENT_ACTION_RECORDED, {"index": 2})
        store.append_event(run.run_id, EventType.ATTEMPT_RECORDED, {"index": 3})
        events = store.list_events(run.run_id)
        index_names = {
            row["name"]
            for row in store._connection.execute("PRAGMA index_list(events)")
        }

    assert [event.sequence for event in events] == [1, 2, 3]
    assert [event.payload for event in events[1:]] == [{"index": 2}, {"index": 3}]
    assert "events_by_run_sequence" in index_names


def test_events_schema_enforces_foreign_key_and_unique_sequence(tmp_path: Path) -> None:
    timestamp = format_timestamp(utc_now())
    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        run = store.create_run()

        with pytest.raises(sqlite3.IntegrityError):
            store._connection.execute(
                "INSERT INTO events "
                "(run_id, sequence, event_type, stage, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("missing", 1, "ATTEMPT_RECORDED", "INTAKE", "{}", timestamp),
            )
        with pytest.raises(sqlite3.IntegrityError):
            store._connection.execute(
                "INSERT INTO events "
                "(run_id, sequence, event_type, stage, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (run.run_id, 1, "ATTEMPT_RECORDED", "INTAKE", "{}", timestamp),
            )


def test_events_database_triggers_reject_mutation(tmp_path: Path) -> None:
    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        run = store.create_run()
        event_id = store._connection.execute(
            "SELECT event_id FROM events WHERE run_id = ?", (run.run_id,)
        ).fetchone()["event_id"]

        with pytest.raises(sqlite3.IntegrityError, match="events are append-only"):
            store._connection.execute(
                "UPDATE events SET payload_json = ? WHERE event_id = ?",
                ("{}", event_id),
            )
        with pytest.raises(sqlite3.IntegrityError, match="events are append-only"):
            store._connection.execute(
                "DELETE FROM events WHERE event_id = ?", (event_id,)
            )


def test_runs_database_constraint_rejects_outcome_before_done(tmp_path: Path) -> None:
    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        run = store.create_run()

        with pytest.raises(sqlite3.IntegrityError):
            store._connection.execute(
                "UPDATE runs SET outcome = ? WHERE run_id = ?",
                (RunOutcome.FAILED.value, run.run_id),
            )


def test_json_context_round_trips_with_deterministic_storage(tmp_path: Path) -> None:
    context = {"z": [True, None, {"b": 2, "a": 1}], "a": "metadata"}
    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        run = store.create_run(context=context)
        raw_context = store._connection.execute(
            "SELECT context_json FROM runs WHERE run_id = ?", (run.run_id,)
        ).fetchone()["context_json"]
        reloaded = store.get_run(run.run_id)

    assert raw_context == '{"a":"metadata","z":[true,null,{"a":1,"b":2}]}'
    assert reloaded.context == context


def test_typed_action_attempt_tool_call_and_result_events(tmp_path: Path) -> None:
    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        run = store.create_run()
        _advance(
            store,
            run.run_id,
            [Stage.ANALYZE, Stage.PLAN, Stage.SETUP, Stage.EXECUTE],
        )
        action = AgentAction.create("inspect_repository", {"depth": 2})
        attempt = Attempt.create(
            "mock_execution",
            Stage.EXECUTE,
            {"command": "python -m pytest"},
        )
        call = ToolCall.create("mock_executor", {"command": "pytest -q"})
        result = ToolResult.create(call.call_id, succeeded=False, payload=False)

        store.record_agent_action(run.run_id, action)
        store.record_attempt(run.run_id, attempt)
        store.record_tool_call(run.run_id, call)
        store.record_tool_result(run.run_id, result)
        events = store.list_events(run.run_id)

    assert [event.event_type for event in events[-4:]] == [
        EventType.AGENT_ACTION_RECORDED,
        EventType.ATTEMPT_RECORDED,
        EventType.TOOL_CALLED,
        EventType.TOOL_RESULT_RECORDED,
    ]
    assert events[-4].payload["agent_action"] == action.to_dict()
    assert events[-3].payload["attempt"] == attempt.to_dict()
    assert events[-2].payload["tool_call"] == call.to_dict()
    assert events[-1].payload["tool_result"] == result.to_dict()
    assert events[-1].payload["tool_result"]["payload"] is False


@pytest.mark.parametrize(
    "operation",
    [
        lambda store: store.get_run("missing"),
        lambda store: store.list_events("missing"),
        lambda store: store.append_event("missing", EventType.ATTEMPT_RECORDED, {}),
    ],
)
def test_missing_runs_raise_typed_error(tmp_path: Path, operation) -> None:
    with (
        SQLiteRunStore(tmp_path / "state.sqlite3") as store,
        pytest.raises(RunNotFoundError, match="missing"),
    ):
        operation(store)


def test_duplicate_run_id_is_reported_as_persistence_error(tmp_path: Path) -> None:
    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        store.create_run(run_id="duplicate")

        with pytest.raises(PersistenceError):
            store.create_run(run_id="duplicate")


def test_corrupt_persisted_stage_fails_loudly(tmp_path: Path) -> None:
    with SQLiteRunStore(tmp_path / "state.sqlite3") as store:
        run = store.create_run()
        store._connection.execute(
            "UPDATE runs SET stage = ? WHERE run_id = ?",
            ("CORRUPT", run.run_id),
        )

        with pytest.raises(PersistenceError, match="run state is invalid"):
            store.get_run(run.run_id)


def test_file_backed_mock_run_survives_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "mock-run.sqlite3"
    context = {
        "repository": "https://github.com/example/mock-project",
        "revision": "deadbeef",
        "mode": "mock",
    }

    with SQLiteRunStore(database_path) as store:
        run = store.create_run(context=context, run_id="mock-run")
        _advance(
            store,
            run.run_id,
            [Stage.ANALYZE, Stage.PLAN, Stage.SETUP, Stage.EXECUTE],
        )
        action = AgentAction.create("mock_analyze", {"source": "fixture"})
        attempt = Attempt.create(
            "mock_execution",
            Stage.EXECUTE,
            {"command": "python -m pytest"},
        )
        tool_call = ToolCall.create("mock_executor", {"command": "pytest -q"})
        tool_result = ToolResult.create(
            tool_call.call_id,
            succeeded=True,
            payload={"exit_code": 0, "stdout": "12 passed"},
        )
        store.record_agent_action(run.run_id, action)
        store.record_attempt(run.run_id, attempt)
        store.record_tool_call(run.run_id, tool_call)
        store.record_tool_result(run.run_id, tool_result)
        store.transition(run.run_id, Stage.DEBUG)
        store.record_attempt(
            run.run_id,
            Attempt.create("mock_debug", Stage.DEBUG, {"change": "retry"}),
        )
        _advance(
            store,
            run.run_id,
            [Stage.EXECUTE, Stage.VERIFY, Stage.REPORT],
        )
        finished = store.finish(run.run_id, RunOutcome.SUCCEEDED)
        before_restart = store.list_events(run.run_id)

    with SQLiteRunStore(database_path) as reopened:
        resumed = reopened.get_run("mock-run")
        after_restart = reopened.list_events("mock-run")

    assert database_path.is_file()
    assert finished.stage is Stage.DONE
    assert resumed.stage is Stage.DONE
    assert resumed.outcome is RunOutcome.SUCCEEDED
    assert resumed.context == context
    assert after_restart == before_restart
    assert [event.sequence for event in after_restart] == list(
        range(1, len(after_restart) + 1)
    )
    assert [event.event_type for event in after_restart] == [
        EventType.RUN_CREATED,
        EventType.STAGE_TRANSITIONED,
        EventType.STAGE_TRANSITIONED,
        EventType.STAGE_TRANSITIONED,
        EventType.STAGE_TRANSITIONED,
        EventType.AGENT_ACTION_RECORDED,
        EventType.ATTEMPT_RECORDED,
        EventType.TOOL_CALLED,
        EventType.TOOL_RESULT_RECORDED,
        EventType.STAGE_TRANSITIONED,
        EventType.ATTEMPT_RECORDED,
        EventType.STAGE_TRANSITIONED,
        EventType.STAGE_TRANSITIONED,
        EventType.STAGE_TRANSITIONED,
        EventType.RUN_FINISHED,
    ]
    transition_path = [
        (
            event.payload["from_stage"],
            event.payload["to_stage"],
        )
        for event in after_restart
        if event.event_type is EventType.STAGE_TRANSITIONED
    ]
    assert transition_path == [
        ("INTAKE", "ANALYZE"),
        ("ANALYZE", "PLAN"),
        ("PLAN", "SETUP"),
        ("SETUP", "EXECUTE"),
        ("EXECUTE", "DEBUG"),
        ("DEBUG", "EXECUTE"),
        ("EXECUTE", "VERIFY"),
        ("VERIFY", "REPORT"),
    ]
    assert after_restart[6].payload["attempt"]["attempt_id"] == attempt.attempt_id
    assert after_restart[7].payload["tool_call"]["call_id"] == tool_call.call_id
    assert after_restart[8].payload["tool_result"]["result_id"] == tool_result.result_id
    assert after_restart[-1].payload == {"outcome": RunOutcome.SUCCEEDED.value}
