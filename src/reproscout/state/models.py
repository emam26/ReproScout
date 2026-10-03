"""Immutable domain models for the ReproScout control plane."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TypeAlias

from reproscout.security import reject_sensitive_mapping

JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]


class Stage(StrEnum):
    """The ordered lifecycle stages of a reproduction run."""

    INTAKE = "INTAKE"
    ANALYZE = "ANALYZE"
    PLAN = "PLAN"
    SETUP = "SETUP"
    EXECUTE = "EXECUTE"
    DEBUG = "DEBUG"
    VERIFY = "VERIFY"
    REPORT = "REPORT"
    DONE = "DONE"


class RunOutcome(StrEnum):
    """Final outcomes recorded only when a run reaches DONE."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class EventType(StrEnum):
    """Minimal audit events emitted by the Phase 3 control plane."""

    RUN_CREATED = "RUN_CREATED"
    STAGE_TRANSITIONED = "STAGE_TRANSITIONED"
    AGENT_ACTION_RECORDED = "AGENT_ACTION_RECORDED"
    ATTEMPT_RECORDED = "ATTEMPT_RECORDED"
    TOOL_CALLED = "TOOL_CALLED"
    TOOL_RESULT_RECORDED = "TOOL_RESULT_RECORDED"
    RUN_FINISHED = "RUN_FINISHED"


def utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""

    return datetime.now(UTC)


def ensure_utc(timestamp: datetime) -> datetime:
    """Normalize and validate a timezone-aware timestamp as UTC."""

    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Timestamps must be timezone-aware.")
    return timestamp.astimezone(UTC)


def format_timestamp(timestamp: datetime) -> str:
    """Serialize a timezone-aware timestamp in a stable UTC format."""

    return (
        ensure_utc(timestamp).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


def parse_timestamp(value: str) -> datetime:
    """Parse a persisted UTC timestamp."""

    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid timestamp: {value!r}") from exc
    return ensure_utc(timestamp)


def canonical_json(value: JSONValue | Mapping[str, JSONValue]) -> str:
    """Serialize JSON-compatible data deterministically."""

    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Value must be JSON-compatible.") from exc


def normalize_json_value(value: JSONValue | Mapping[str, JSONValue]) -> JSONValue:
    """Validate and detach JSON-compatible data from caller-owned objects."""

    normalized: JSONValue = json.loads(canonical_json(value))
    if isinstance(normalized, dict):
        reject_sensitive_mapping(normalized)
    return normalized


def normalize_json_object(
    value: Mapping[str, JSONValue] | None,
) -> dict[str, JSONValue]:
    """Validate JSON-compatible object metadata."""

    normalized = normalize_json_value(value or {})
    if not isinstance(normalized, dict):
        raise TypeError("Metadata must be a JSON object.")
    return normalized


def new_id() -> str:
    """Create a collision-resistant identifier independent of timestamps."""

    return uuid.uuid4().hex


@dataclass(frozen=True, slots=True)
class RunState:
    """Current immutable snapshot of a reproduction run."""

    run_id: str
    stage: Stage
    outcome: RunOutcome | None
    created_at: datetime
    updated_at: datetime
    context: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ValueError("run_id cannot be empty.")
        created_at = ensure_utc(self.created_at)
        updated_at = ensure_utc(self.updated_at)
        if updated_at < created_at:
            raise ValueError("updated_at cannot be earlier than created_at.")
        if self.stage is Stage.DONE and self.outcome is None:
            raise ValueError("DONE runs require a final outcome.")
        if self.stage is not Stage.DONE and self.outcome is not None:
            raise ValueError("Only DONE runs may have a final outcome.")
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "updated_at", updated_at)
        object.__setattr__(self, "context", normalize_json_object(self.context))


@dataclass(frozen=True, slots=True)
class AgentAction:
    """A future control-plane action, independent of any LLM provider."""

    action_id: str
    action_type: str
    arguments: Mapping[str, JSONValue]
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.action_id or not self.action_type:
            raise ValueError("AgentAction identifiers and types cannot be empty.")
        object.__setattr__(self, "arguments", normalize_json_object(self.arguments))
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))

    @classmethod
    def create(
        cls,
        action_type: str,
        arguments: Mapping[str, JSONValue] | None = None,
    ) -> AgentAction:
        return cls(new_id(), action_type, arguments or {}, utc_now())

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "action_id": self.action_id,
            "action_type": self.action_type,
            "arguments": dict(self.arguments),
            "created_at": format_timestamp(self.created_at),
        }


@dataclass(frozen=True, slots=True)
class ToolCall:
    """An intended invocation of a controlled tool."""

    call_id: str
    tool_name: str
    arguments: Mapping[str, JSONValue]
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.call_id or not self.tool_name:
            raise ValueError("ToolCall identifiers and tool names cannot be empty.")
        object.__setattr__(self, "arguments", normalize_json_object(self.arguments))
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))

    @classmethod
    def create(
        cls,
        tool_name: str,
        arguments: Mapping[str, JSONValue] | None = None,
    ) -> ToolCall:
        return cls(new_id(), tool_name, arguments or {}, utc_now())

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "arguments": dict(self.arguments),
            "created_at": format_timestamp(self.created_at),
        }


@dataclass(frozen=True, slots=True)
class ToolResult:
    """A generic result associated with one ToolCall."""

    result_id: str
    call_id: str
    succeeded: bool
    payload: JSONValue
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.result_id or not self.call_id:
            raise ValueError("ToolResult identifiers cannot be empty.")
        object.__setattr__(self, "payload", normalize_json_value(self.payload))
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))

    @classmethod
    def create(
        cls,
        call_id: str,
        *,
        succeeded: bool,
        payload: JSONValue | Mapping[str, JSONValue] | None = None,
    ) -> ToolResult:
        return cls(
            new_id(),
            call_id,
            succeeded,
            {} if payload is None else payload,
            utc_now(),
        )

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "result_id": self.result_id,
            "call_id": self.call_id,
            "succeeded": self.succeeded,
            "payload": self.payload,
            "created_at": format_timestamp(self.created_at),
        }


@dataclass(frozen=True, slots=True)
class Attempt:
    """A distinct execution, debugging, or verification attempt."""

    attempt_id: str
    attempt_type: str
    stage: Stage
    metadata: Mapping[str, JSONValue]
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.attempt_id or not self.attempt_type:
            raise ValueError("Attempt identifiers and types cannot be empty.")
        object.__setattr__(self, "metadata", normalize_json_object(self.metadata))
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))

    @classmethod
    def create(
        cls,
        attempt_type: str,
        stage: Stage,
        metadata: Mapping[str, JSONValue] | None = None,
    ) -> Attempt:
        return cls(new_id(), attempt_type, stage, metadata or {}, utc_now())

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "attempt_id": self.attempt_id,
            "attempt_type": self.attempt_type,
            "stage": self.stage.value,
            "metadata": dict(self.metadata),
            "created_at": format_timestamp(self.created_at),
        }


@dataclass(frozen=True, slots=True)
class Event:
    """Immutable, deterministically ordered audit record."""

    run_id: str
    sequence: int
    event_type: EventType
    stage: Stage
    timestamp: datetime
    payload: JSONValue

    def __post_init__(self) -> None:
        if not self.run_id or self.sequence < 1:
            raise ValueError("Events require a run ID and positive sequence number.")
        object.__setattr__(self, "timestamp", ensure_utc(self.timestamp))
        object.__setattr__(self, "payload", normalize_json_value(self.payload))
