# State and Events

Phase 3 adds ReproScout's local control plane: typed lifecycle state, a narrow
action/evidence vocabulary, and durable audit history. It intentionally does
not add an LLM provider, planning policy, autonomous execution, repair logic,
or new sandbox behavior.

This is a control-plane persistence layer. It is NOT yet an autonomous agent.

## Lifecycle

Each run has one current `RunState` with a `Stage` and, once terminal, a
`RunOutcome`.

```text
INTAKE -> ANALYZE -> PLAN -> SETUP -> EXECUTE
                                      |       \
                                      v        v
                                    DEBUG -> VERIFY -> REPORT -> DONE
                                      |        ^
                                      +-> EXECUTE
```

The exact legal edges are:

```text
INTAKE  -> ANALYZE
ANALYZE -> PLAN
PLAN    -> SETUP
SETUP   -> EXECUTE
EXECUTE -> DEBUG | VERIFY
DEBUG   -> EXECUTE | VERIFY
VERIFY  -> DEBUG | REPORT
REPORT  -> DONE
DONE    -> (none)
```

`transition()` rejects any edge outside this graph with
`InvalidTransitionError`, including transitions out of `DONE`. `finish()` is
the only way to enter `DONE`; it requires `REPORT` and a final outcome of
`SUCCEEDED`, `FAILED`, or `CANCELLED`. A final outcome cannot exist before
`DONE`.

## Typed records

The public types live in `reproscout.state`:

* `RunState` and `Stage` describe the current lifecycle snapshot.
* `RunOutcome` records the terminal result.
* `AgentAction`, `Attempt`, `ToolCall`, and `ToolResult` capture structured
  control-plane intent and evidence without coupling to an LLM or executor.
* `Event` is an ordered audit record. `EventType` includes run creation,
  stage transitions, actions, attempts, tool calls/results, and run finish.

All persisted metadata is JSON-compatible. JSON is normalized and serialized
with sorted keys and compact separators; timestamps are timezone-aware UTC
strings with microsecond precision and a `Z` suffix.

## SQLite store

`SQLiteRunStore` persists state to a caller-chosen SQLite database path.
`runs` holds the latest snapshot; `events` holds ordered history. This is a
snapshot-plus-audit design, not full event sourcing: run snapshots are the
authoritative current state, and the system does not rebuild them by replaying
events.

The store enables foreign keys and enforces:

* one run snapshot per `run_id`;
* `events.run_id` foreign keys to `runs`;
* unique, monotonic `(run_id, sequence)` event positions with an indexed
  retrieval path;
* database triggers that reject `UPDATE` and `DELETE` against `events`;
* a database constraint matching the terminal-outcome invariant.

Run creation, each state transition, and finish operation occur in one SQLite
transaction. A transition updates the snapshot and appends its event together;
an invalid transition changes neither. The public store supports appending and
listing events, but exposes no event update or delete operation.

```python
from pathlib import Path

from reproscout.state import RunOutcome, SQLiteRunStore, Stage

with SQLiteRunStore(Path("runs") / "example.sqlite3") as store:
    run = store.create_run(context={"repository": "owner/project"})
    store.transition(run.run_id, Stage.ANALYZE)
    # Continue through the legal lifecycle edges.
```

Use `record_agent_action`, `record_attempt`, `record_tool_call`, and
`record_tool_result` to append the corresponding typed audit records. The
file-backed restart acceptance test in `tests/test_state.py` demonstrates a
mock run with an execute/debug/verify path, typed records, terminal outcome,
and history retained after reopening the database.

## Restart semantics and limitations

Restart recovery means closing the store and opening a brand-new
`SQLiteRunStore` against the same database file. The current `RunState` and
complete ordered event history can then be reloaded.

Phase 3 does not resume Docker containers, reconnect to execution processes,
or orchestrate crash recovery. It also does not execute tools, choose actions,
reason about failures, verify reproduction success, or rebuild state by
replaying events. Those responsibilities belong to later phases.

## Safety and scope boundary

State records are metadata only. Phase 3 neither invokes Docker nor changes
the Phase 2 sandbox package, its resource limits, mounts, capability policy,
network policy, or cleanup behavior. Future phases may use this control plane,
but they must preserve those execution-safety guarantees.
