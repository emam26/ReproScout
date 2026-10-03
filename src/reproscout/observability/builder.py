"""Deterministic observability projection over persisted run events."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from itertools import pairwise
from typing import Any

from reproscout.security import redact_sensitive_text
from reproscout.state import Event, EventType
from reproscout.status import ReproductionStatusResult
from reproscout.verification import VerificationResult

from .models import RunMetricSummary, RunObservability, StageMetric, TimelineEntry


class ObservabilityError(ValueError):
    """Raised when an event stream cannot form a stable metrics projection."""


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _nested_mapping(value: object, key: str) -> Mapping[str, Any]:
    return _mapping(_mapping(value).get(key))


def _safe_label(value: object, fallback: str) -> str:
    if not isinstance(value, str) or not value:
        return fallback
    return redact_sensitive_text(value)[:180]


def _timeline_summary(event: Event) -> str:
    payload = _mapping(event.payload)
    if event.event_type is EventType.STAGE_TRANSITIONED:
        from_stage = _safe_label(payload.get("from_stage"), "unknown")
        to_stage = _safe_label(payload.get("to_stage"), event.stage.value)
        return f"stage transitioned: {from_stage} -> {to_stage}"
    if event.event_type is EventType.AGENT_ACTION_RECORDED:
        action = _nested_mapping(payload, "agent_action")
        action_type = _safe_label(action.get("action_type"), "unknown")
        return f"agent action: {action_type}"
    if event.event_type is EventType.ATTEMPT_RECORDED:
        attempt = _nested_mapping(payload, "attempt")
        attempt_type = _safe_label(attempt.get("attempt_type"), "unknown")
        return f"attempt: {attempt_type}"
    if event.event_type is EventType.TOOL_CALLED:
        call = _nested_mapping(payload, "tool_call")
        tool_name = _safe_label(call.get("tool_name"), "unknown")
        return f"tool called: {tool_name}"
    if event.event_type is EventType.TOOL_RESULT_RECORDED:
        result = _nested_mapping(payload, "tool_result")
        result_payload = _mapping(result.get("payload"))
        failure = result_payload.get("failure_kind")
        if isinstance(failure, str) and failure:
            return f"tool result: failed ({_safe_label(failure, 'unknown')})"
        return (
            "tool result: succeeded"
            if result.get("succeeded")
            else "tool result: failed"
        )
    if event.event_type is EventType.RUN_FINISHED:
        outcome = _safe_label(payload.get("outcome"), "unknown")
        return f"run finished: {outcome}"
    if event.event_type is EventType.RUN_CREATED:
        return "run created"
    return event.event_type.value.lower().replace("_", " ")


def _repair_count(events: Iterable[Event]) -> int:
    attempt_count = 0
    action_count = 0
    for event in events:
        payload = _mapping(event.payload)
        if event.event_type is EventType.ATTEMPT_RECORDED:
            attempt = _nested_mapping(payload, "attempt")
            attempt_type = attempt.get("attempt_type")
            if isinstance(attempt_type, str) and attempt_type.lower().startswith(
                "repair"
            ):
                attempt_count += 1
        elif event.event_type is EventType.AGENT_ACTION_RECORDED:
            action = _nested_mapping(payload, "agent_action")
            action_type = action.get("action_type")
            if isinstance(action_type, str) and action_type.lower().startswith(
                "repair"
            ):
                action_count += 1
    return max(attempt_count, action_count)


def _llm_metrics(events: Iterable[Event]) -> tuple[int, int, int, bool]:
    calls = 0
    input_tokens = 0
    output_tokens = 0
    complete = True
    for event in events:
        if event.event_type is not EventType.AGENT_ACTION_RECORDED:
            continue
        action = _nested_mapping(_mapping(event.payload), "agent_action")
        if action.get("action_type") != "diagnosis_result":
            continue
        arguments = _mapping(action.get("arguments"))
        if arguments.get("status") != "LLM":
            continue
        calls += 1
        usage = _mapping(arguments.get("usage"))
        input_value = usage.get("input_tokens")
        output_value = usage.get("output_tokens")
        if isinstance(input_value, int) and input_value >= 0:
            input_tokens += input_value
        else:
            complete = False
        if isinstance(output_value, int) and output_value >= 0:
            output_tokens += output_value
        else:
            complete = False
    return calls, input_tokens, output_tokens, complete


def _failure_categories(events: Iterable[Event]) -> dict[str, int]:
    categories: Counter[str] = Counter()
    for event in events:
        payload = _mapping(event.payload)
        if event.event_type is EventType.TOOL_RESULT_RECORDED:
            result = _nested_mapping(payload, "tool_result")
            result_payload = _mapping(result.get("payload"))
            failure = result_payload.get("failure_kind")
            if isinstance(failure, str) and failure:
                categories[failure] += 1
        elif event.event_type is EventType.AGENT_ACTION_RECORDED:
            action = _nested_mapping(payload, "agent_action")
            if action.get("action_type") != "diagnosis_result":
                continue
            arguments = _mapping(action.get("arguments"))
            diagnosis = _mapping(arguments.get("diagnosis"))
            failure = diagnosis.get("failure_class")
            if isinstance(failure, str) and failure:
                categories[failure] += 1
    return dict(sorted(categories.items()))


def _docker_observations(events: Iterable[Event]) -> tuple[list[str], list[str]]:
    container_ids: list[str] = []
    network_modes: list[str] = []

    def add_unique(target: list[str], value: object) -> None:
        if isinstance(value, str) and value and value not in target:
            target.append(value)

    for event in events:
        payload = _mapping(event.payload)
        if event.event_type is EventType.TOOL_CALLED:
            call = _nested_mapping(payload, "tool_call")
            arguments = _mapping(call.get("arguments"))
            add_unique(network_modes, arguments.get("network_mode"))
        elif event.event_type is EventType.TOOL_RESULT_RECORDED:
            result = _nested_mapping(payload, "tool_result")
            result_payload = _mapping(result.get("payload"))
            add_unique(container_ids, result_payload.get("container_id"))
            add_unique(network_modes, result_payload.get("network_mode"))
    return container_ids, network_modes


def _validate_events(events: list[Event]) -> list[Event]:
    if not events:
        raise ObservabilityError("At least one persisted event is required.")
    ordered = sorted(events, key=lambda event: event.sequence)
    run_id = ordered[0].run_id
    expected_sequence = list(range(1, len(ordered) + 1))
    if any(event.run_id != run_id for event in ordered):
        raise ObservabilityError("An observability stream cannot mix run IDs.")
    if [event.sequence for event in ordered] != expected_sequence:
        raise ObservabilityError("Event sequences must be contiguous and unique.")
    if any(later.timestamp < earlier.timestamp for earlier, later in pairwise(ordered)):
        raise ObservabilityError("Event timestamps must be non-decreasing.")
    return ordered


def build_run_observability(
    events: Iterable[Event],
    *,
    verification: VerificationResult | None = None,
    final_status: ReproductionStatusResult | None = None,
) -> RunObservability:
    """Build bounded metrics and a stable timeline from append-only events.

    No target command is executed and event payloads are never copied into the
    returned timeline. Optional verification and status objects are supplied by
    their deterministic engines when those results are available.
    """

    ordered = _validate_events(list(events))
    first = ordered[0].timestamp
    last = ordered[-1].timestamp
    duration = (last - first).total_seconds()
    stage_durations: dict[object, float] = defaultdict(float)
    stage_counts: Counter[object] = Counter()
    for event in ordered:
        stage_counts[event.stage] += 1
    for current, following in pairwise(ordered):
        stage_durations[current.stage] += (
            following.timestamp - current.timestamp
        ).total_seconds()
    stage_metrics = [
        StageMetric(
            stage=stage,
            duration_seconds=stage_durations[stage],
            event_count=stage_counts[stage],
        )
        for stage in dict.fromkeys(event.stage for event in ordered)
    ]
    llm_calls, input_tokens, output_tokens, token_usage_complete = _llm_metrics(ordered)
    container_ids, network_modes = _docker_observations(ordered)
    timeline = [
        TimelineEntry(
            sequence=event.sequence,
            event_type=event.event_type,
            stage=event.stage,
            timestamp=event.timestamp,
            summary=_timeline_summary(event),
        )
        for event in ordered
    ]
    metrics = RunMetricSummary(
        run_id=ordered[0].run_id,
        duration_seconds=duration,
        stage_metrics=stage_metrics,
        attempt_count=sum(
            event.event_type is EventType.ATTEMPT_RECORDED for event in ordered
        ),
        repair_count=_repair_count(ordered),
        llm_calls=llm_calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        token_usage_complete=token_usage_complete,
        failure_categories=_failure_categories(ordered),
        verification_level=verification.level if verification is not None else None,
        final_status=final_status.status if final_status is not None else None,
        docker_container_ids=container_ids,
        network_modes=network_modes,
    )
    return RunObservability(
        run_id=ordered[0].run_id,
        metrics=metrics,
        timeline=timeline,
    )
