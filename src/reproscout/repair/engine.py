"""Bounded hypothesis → repair → execution → observation experiments."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Protocol

from reproscout.diagnostics import DiagnosticContext
from reproscout.state import AgentAction, Attempt, RunStore, Stage

from .models import (
    RepairAction,
    RepairActionType,
    RepairExperimentResult,
    RepairExperimentStatus,
    RepairLimits,
    RepairObservation,
)
from .plan import RepairNotExecutableError
from .policy import RepairPolicyError, validate_repair_action


class RepairBackend(Protocol):
    def apply(self, action: RepairAction) -> RepairApplication: ...


class RepairApplication(Protocol):
    def rollback(self) -> object: ...


ObservationFunction = Callable[[RepairApplication], RepairObservation]


class RepairExperimentEngine:
    """Run finite deterministic repair experiments and preserve an audit trail."""

    def __init__(
        self,
        store: RunStore,
        backend: RepairBackend,
        *,
        limits: RepairLimits | None = None,
    ) -> None:
        self.store = store
        self.backend = backend
        self.limits = limits or RepairLimits()
        self.repair_count = 0
        self.failure_signatures: Counter[str] = Counter()

    def run(
        self,
        action: RepairAction,
        context: DiagnosticContext,
        *,
        before_failure_signature: str | None,
        observe: ObservationFunction,
        run_id: str | None = None,
    ) -> RepairExperimentResult:
        """Apply one typed repair, observe objectively, and always roll it back."""

        try:
            validate_repair_action(action, context, limits=self.limits)
        except RepairPolicyError as exc:
            result = RepairExperimentResult(
                action=action,
                status=RepairExperimentStatus.REJECTED,
                before_failure_signature=before_failure_signature,
                policy_rejection=str(exc)[:1_000],
            )
            self._record(run_id, "repair_rejected", result)
            return result
        if self.repair_count >= self.limits.max_repairs:
            return self._stopped(
                action, before_failure_signature, "Maximum repairs reached.", run_id
            )
        if (
            before_failure_signature is not None
            and self.failure_signatures[before_failure_signature]
            >= self.limits.max_repeated_failure_signatures
        ):
            return self._stopped(
                action,
                before_failure_signature,
                "Repeated failure signature blocked another repair.",
                run_id,
            )
        if action.action_type is RepairActionType.STOP_UNREPAIRABLE:
            return self._stopped(
                action, before_failure_signature, "Diagnosis requested a stop.", run_id
            )
        self.repair_count += 1
        self._record(run_id, "repair_proposed", action.model_dump(mode="json"))
        try:
            application = self.backend.apply(action)
        except RepairNotExecutableError as exc:
            result = RepairExperimentResult(
                action=action,
                status=RepairExperimentStatus.DEFERRED,
                before_failure_signature=before_failure_signature,
                stop_reason=str(exc)[:1_000],
            )
            self._record(run_id, "repair_deferred", result)
            return result
        except RepairPolicyError as exc:
            result = RepairExperimentResult(
                action=action,
                status=RepairExperimentStatus.REJECTED,
                before_failure_signature=before_failure_signature,
                policy_rejection=str(exc)[:1_000],
            )
            self._record(run_id, "repair_rejected", result)
            return result
        self._record(
            run_id, "repair_applied", {"action": action.model_dump(mode="json")}
        )
        try:
            observation = observe(application)
            after_signature = observation.failure_signature
            improved = observation.workflow_succeeded or (
                after_signature is not None
                and after_signature != before_failure_signature
            )
            if before_failure_signature is not None:
                self.failure_signatures[before_failure_signature] += 1
            result = RepairExperimentResult(
                action=action,
                status=(
                    RepairExperimentStatus.IMPROVED
                    if improved
                    else RepairExperimentStatus.UNCHANGED
                ),
                before_failure_signature=before_failure_signature,
                after_failure_signature=after_signature,
                observation=observation,
                evidence_improved=improved,
                rollback_performed=True,
            )
        finally:
            application.rollback()
        self._record(run_id, "repair_observed", result)
        return result

    def _stopped(
        self,
        action: RepairAction,
        before_signature: str | None,
        reason: str,
        run_id: str | None,
    ) -> RepairExperimentResult:
        result = RepairExperimentResult(
            action=action,
            status=RepairExperimentStatus.STOPPED,
            before_failure_signature=before_signature,
            stop_reason=reason,
        )
        self._record(run_id, "repair_stopped", result)
        return result

    def _record(self, run_id: str | None, name: str, payload: object) -> None:
        if run_id is None:
            return
        self.store.record_attempt(
            run_id,
            Attempt.create("repair", Stage.DEBUG, {"event": name}),
        )
        if isinstance(payload, (RepairExperimentResult, RepairAction)):
            value = payload.model_dump(mode="json")
        else:
            value = payload
        self.store.record_agent_action(
            run_id,
            AgentAction.create(name, {"repair": value}),
        )
