"""Persistence interface for the Phase 3 run control plane."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping

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
)


class RunNotFoundError(KeyError):
    """Raised when a requested run ID has no persisted state."""


class PersistenceError(RuntimeError):
    """Raised when persisted state is invalid or SQLite cannot complete an operation."""


class RunStore(ABC):
    """Minimal persistence contract for run snapshots and append-only events."""

    @abstractmethod
    def create_run(
        self,
        *,
        context: Mapping[str, JSONValue] | None = None,
        run_id: str | None = None,
    ) -> RunState:
        """Create a run at INTAKE and append its creation event."""

    @abstractmethod
    def get_run(self, run_id: str) -> RunState:
        """Load a persisted run snapshot."""

    @abstractmethod
    def list_runs(self) -> list[RunState]:
        """Load all persisted run snapshots in creation order."""

    @abstractmethod
    def transition(self, run_id: str, requested_stage: Stage) -> RunState:
        """Atomically transition a run and append a transition event."""

    @abstractmethod
    def finish(self, run_id: str, outcome: RunOutcome) -> RunState:
        """Atomically finish a run and append its final event."""

    @abstractmethod
    def append_event(
        self,
        run_id: str,
        event_type: EventType,
        payload: JSONValue,
    ) -> Event:
        """Append an event at the run's current stage."""

    @abstractmethod
    def list_events(self, run_id: str) -> list[Event]:
        """Load a run's audit history in sequence order."""

    @abstractmethod
    def record_agent_action(self, run_id: str, action: AgentAction) -> Event:
        """Append a future control-plane action event."""

    @abstractmethod
    def record_attempt(self, run_id: str, attempt: Attempt) -> Event:
        """Append an execution, debug, or verification attempt event."""

    @abstractmethod
    def record_tool_call(self, run_id: str, tool_call: ToolCall) -> Event:
        """Append an intended controlled-tool invocation event."""

    @abstractmethod
    def record_tool_result(self, run_id: str, tool_result: ToolResult) -> Event:
        """Append a controlled-tool result event."""

    @abstractmethod
    def close(self) -> None:
        """Release persistence resources."""
