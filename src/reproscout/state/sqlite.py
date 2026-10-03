"""SQLite persistence for run snapshots and append-only audit events."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Self

from .machine import finish as finish_state
from .machine import transition as transition_state
from .models import (
    AgentAction,
    Attempt,
    Event,
    EventType,
    JSONValue,
    RunOutcome,
    RunState,
    Stage,
    ToolCall,
    ToolResult,
    canonical_json,
    format_timestamp,
    new_id,
    normalize_json_value,
    parse_timestamp,
    utc_now,
)
from .store import PersistenceError, RunNotFoundError, RunStore

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    stage TEXT NOT NULL,
    outcome TEXT NULL,
    context_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        (stage = 'DONE' AND outcome IS NOT NULL)
        OR (stage <> 'DONE' AND outcome IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    stage TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id),
    UNIQUE (run_id, sequence)
);

CREATE INDEX IF NOT EXISTS events_by_run_sequence
ON events(run_id, sequence);

CREATE TRIGGER IF NOT EXISTS events_are_append_only_update
BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS events_are_append_only_delete
BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only');
END;
"""


class SQLiteRunStore(RunStore):
    """File-backed SQLite store with transactional state and audit updates."""

    def __init__(self, database_path: str | Path) -> None:
        database_value = str(database_path)
        if database_value == ":memory:":
            self.database_path: Path | None = None
        else:
            self.database_path = Path(database_path).expanduser().resolve()
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            database_value = str(self.database_path)
        try:
            self._connection = sqlite3.connect(database_value, isolation_level=None)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.executescript(_SCHEMA)
        except sqlite3.Error as exc:
            raise PersistenceError("Could not initialize SQLite run store.") from exc

    def close(self) -> None:
        """Close the underlying SQLite connection."""

        self._connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            yield
        except sqlite3.Error as exc:
            self._connection.rollback()
            raise PersistenceError(
                "SQLite operation failed and was rolled back."
            ) from exc
        except Exception:
            self._connection.rollback()
            raise
        else:
            try:
                self._connection.commit()
            except sqlite3.Error as exc:
                self._connection.rollback()
                raise PersistenceError(
                    "SQLite commit failed and was rolled back."
                ) from exc

    def _load_run(self, run_id: str) -> RunState:
        row = self._connection.execute(
            "SELECT run_id, stage, outcome, context_json, created_at, updated_at "
            "FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise RunNotFoundError(run_id)
        return self._run_state_from_row(row)

    @staticmethod
    def _json_from_storage(value: str, *, expected_object: bool = False) -> JSONValue:
        try:
            parsed = json.loads(value)
            normalized = normalize_json_value(parsed)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PersistenceError("Persisted JSON is invalid.") from exc
        if expected_object and not isinstance(normalized, dict):
            raise PersistenceError("Persisted context must be a JSON object.")
        return normalized

    @staticmethod
    def _run_state_from_row(row: sqlite3.Row) -> RunState:
        try:
            outcome = RunOutcome(row["outcome"]) if row["outcome"] is not None else None
            return RunState(
                run_id=row["run_id"],
                stage=Stage(row["stage"]),
                outcome=outcome,
                context=SQLiteRunStore._json_from_storage(
                    row["context_json"],
                    expected_object=True,
                ),
                created_at=parse_timestamp(row["created_at"]),
                updated_at=parse_timestamp(row["updated_at"]),
            )
        except (TypeError, ValueError) as exc:
            raise PersistenceError("Persisted run state is invalid.") from exc

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> Event:
        try:
            return Event(
                run_id=row["run_id"],
                sequence=row["sequence"],
                event_type=EventType(row["event_type"]),
                stage=Stage(row["stage"]),
                timestamp=parse_timestamp(row["created_at"]),
                payload=SQLiteRunStore._json_from_storage(row["payload_json"]),
            )
        except (TypeError, ValueError) as exc:
            raise PersistenceError("Persisted event is invalid.") from exc

    def _append_event_in_transaction(
        self,
        state: RunState,
        event_type: EventType,
        payload: JSONValue,
        *,
        timestamp: datetime,
    ) -> Event:
        sequence_row = self._connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence "
            "FROM events WHERE run_id = ?",
            (state.run_id,),
        ).fetchone()
        event = Event(
            run_id=state.run_id,
            sequence=int(sequence_row["next_sequence"]),
            event_type=event_type,
            stage=state.stage,
            timestamp=timestamp,
            payload=payload,
        )
        self._connection.execute(
            "INSERT INTO events "
            "(run_id, sequence, event_type, stage, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                event.run_id,
                event.sequence,
                event.event_type.value,
                event.stage.value,
                canonical_json(event.payload),
                format_timestamp(event.timestamp),
            ),
        )
        return event

    def _persist_run_in_transaction(self, state: RunState) -> None:
        self._connection.execute(
            "UPDATE runs SET stage = ?, outcome = ?, context_json = ?, updated_at = ? "
            "WHERE run_id = ?",
            (
                state.stage.value,
                state.outcome.value if state.outcome is not None else None,
                canonical_json(state.context),
                format_timestamp(state.updated_at),
                state.run_id,
            ),
        )

    def create_run(
        self,
        *,
        context: Mapping[str, JSONValue] | None = None,
        run_id: str | None = None,
    ) -> RunState:
        """Create a run snapshot and creation event in a single transaction."""

        timestamp = utc_now()
        state = RunState(
            run_id=new_id() if run_id is None else run_id,
            stage=Stage.INTAKE,
            outcome=None,
            context={} if context is None else context,
            created_at=timestamp,
            updated_at=timestamp,
        )
        with self._transaction():
            self._connection.execute(
                "INSERT INTO runs "
                "(run_id, stage, outcome, context_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    state.run_id,
                    state.stage.value,
                    None,
                    canonical_json(state.context),
                    format_timestamp(state.created_at),
                    format_timestamp(state.updated_at),
                ),
            )
            self._append_event_in_transaction(
                state,
                EventType.RUN_CREATED,
                {"context": dict(state.context)},
                timestamp=timestamp,
            )
        return state

    def get_run(self, run_id: str) -> RunState:
        """Load the current immutable state for one run."""

        try:
            return self._load_run(run_id)
        except sqlite3.Error as exc:
            raise PersistenceError("Could not load persisted run state.") from exc

    def list_runs(self) -> list[RunState]:
        """Load all persisted run snapshots in deterministic creation order."""

        try:
            rows = self._connection.execute(
                "SELECT run_id, stage, outcome, context_json, created_at, updated_at "
                "FROM runs ORDER BY created_at ASC, run_id ASC"
            ).fetchall()
            return [self._run_state_from_row(row) for row in rows]
        except sqlite3.Error as exc:
            raise PersistenceError("Could not list persisted run state.") from exc

    def transition(self, run_id: str, requested_stage: Stage) -> RunState:
        """Atomically persist a legal transition and its audit event."""

        with self._transaction():
            current = self._load_run(run_id)
            updated = transition_state(current, requested_stage)
            self._persist_run_in_transaction(updated)
            self._append_event_in_transaction(
                updated,
                EventType.STAGE_TRANSITIONED,
                {
                    "from_stage": current.stage.value,
                    "to_stage": updated.stage.value,
                },
                timestamp=updated.updated_at,
            )
        return updated

    def finish(self, run_id: str, outcome: RunOutcome) -> RunState:
        """Atomically persist a final outcome and terminal audit event."""

        with self._transaction():
            current = self._load_run(run_id)
            updated = finish_state(current, outcome)
            self._persist_run_in_transaction(updated)
            self._append_event_in_transaction(
                updated,
                EventType.RUN_FINISHED,
                {"outcome": outcome.value},
                timestamp=updated.updated_at,
            )
        return updated

    def append_event(
        self,
        run_id: str,
        event_type: EventType,
        payload: JSONValue,
    ) -> Event:
        """Append an audit event at the run's currently persisted stage."""

        with self._transaction():
            state = self._load_run(run_id)
            event = self._append_event_in_transaction(
                state,
                event_type,
                payload,
                timestamp=utc_now(),
            )
        return event

    def record_agent_action(self, run_id: str, action: AgentAction) -> Event:
        """Append a typed future control-plane action."""

        return self.append_event(
            run_id,
            EventType.AGENT_ACTION_RECORDED,
            {"agent_action": action.to_dict()},
        )

    def record_attempt(self, run_id: str, attempt: Attempt) -> Event:
        """Append a typed attempt record."""

        return self.append_event(
            run_id,
            EventType.ATTEMPT_RECORDED,
            {"attempt": attempt.to_dict()},
        )

    def record_tool_call(self, run_id: str, tool_call: ToolCall) -> Event:
        """Append a typed intended tool invocation."""

        return self.append_event(
            run_id,
            EventType.TOOL_CALLED,
            {"tool_call": tool_call.to_dict()},
        )

    def record_tool_result(self, run_id: str, tool_result: ToolResult) -> Event:
        """Append a typed tool result."""

        return self.append_event(
            run_id,
            EventType.TOOL_RESULT_RECORDED,
            {"tool_result": tool_result.to_dict()},
        )

    def list_events(self, run_id: str) -> list[Event]:
        """Return append-only audit history in deterministic sequence order."""

        try:
            self._load_run(run_id)
            rows = self._connection.execute(
                "SELECT run_id, sequence, event_type, stage, payload_json, created_at "
                "FROM events WHERE run_id = ? ORDER BY sequence ASC",
                (run_id,),
            ).fetchall()
            return [self._event_from_row(row) for row in rows]
        except sqlite3.Error as exc:
            raise PersistenceError("Could not load persisted event history.") from exc
