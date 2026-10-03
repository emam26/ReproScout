"""Legal lifecycle transitions for persisted run state."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from .models import RunOutcome, RunState, Stage, ensure_utc, utc_now


class InvalidTransitionError(ValueError):
    """Raised when a requested lifecycle transition is not legal."""

    def __init__(self, current_stage: Stage, requested_stage: Stage) -> None:
        self.current_stage = current_stage
        self.requested_stage = requested_stage
        super().__init__(
            f"Invalid transition from {current_stage.value} to {requested_stage.value}."
        )


LEGAL_TRANSITIONS: dict[Stage, frozenset[Stage]] = {
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


def validate_transition(current_stage: Stage, requested_stage: Stage) -> None:
    """Raise when a requested transition is absent from the legal graph."""

    if requested_stage not in LEGAL_TRANSITIONS[current_stage]:
        raise InvalidTransitionError(current_stage, requested_stage)


def transition(
    state: RunState,
    requested_stage: Stage,
    *,
    timestamp: datetime | None = None,
) -> RunState:
    """Transition an active run without allowing callers to set final outcomes."""

    validate_transition(state.stage, requested_stage)
    if requested_stage is Stage.DONE:
        raise InvalidTransitionError(state.stage, requested_stage)
    return replace(
        state,
        stage=requested_stage,
        updated_at=ensure_utc(timestamp or utc_now()),
    )


def finish(
    state: RunState,
    outcome: RunOutcome,
    *,
    timestamp: datetime | None = None,
) -> RunState:
    """Finish a REPORT-stage run with its immutable final outcome."""

    validate_transition(state.stage, Stage.DONE)
    return replace(
        state,
        stage=Stage.DONE,
        outcome=outcome,
        updated_at=ensure_utc(timestamp or utc_now()),
    )
