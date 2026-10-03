"""Convert provider-independent AgentDecision output into a strict diagnosis."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import ValidationError

from reproscout.diagnostics import DiagnosticContext, FailureClass
from reproscout.llm import AgentDecision

from .models import Diagnosis, DiagnosisAction, DiagnosisHypothesis, DiagnosisRisk


class InvalidDiagnosisError(ValueError):
    """Raised when a provider decision is not a diagnosis proposal."""


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidDiagnosisError(
            f"Diagnosis field {field!r} must be a non-empty string."
        )
    return value


def parse_diagnosis_decision(
    decision: AgentDecision,
    context: DiagnosticContext,
) -> Diagnosis:
    """Validate all model-produced diagnosis fields through Pydantic."""

    if decision.action != "diagnose":
        raise InvalidDiagnosisError("Provider decision action must be 'diagnose'.")
    arguments = decision.arguments
    raw_failure_class = arguments.get("failure_class", context.failure_class.value)
    raw_action = arguments.get("recommended_action")
    raw_risk = arguments.get("risk")
    raw_hypotheses = arguments.get("hypotheses")
    raw_supporting = arguments.get("supporting_evidence")
    raw_action_arguments = arguments.get("action_arguments", {})
    raw_expected = arguments.get("expected_observation", decision.expected_effect)
    if not isinstance(raw_failure_class, str):
        raise InvalidDiagnosisError("Diagnosis failure_class must be a string.")
    if not isinstance(raw_action, str):
        raise InvalidDiagnosisError("Diagnosis recommended_action is required.")
    if not isinstance(raw_risk, str):
        raise InvalidDiagnosisError("Diagnosis risk is required.")
    if not isinstance(raw_hypotheses, list) or not raw_hypotheses:
        raise InvalidDiagnosisError("Diagnosis requires at least one hypothesis.")
    if not isinstance(raw_supporting, list) or not raw_supporting:
        raise InvalidDiagnosisError(
            "Diagnosis requires supporting evidence references."
        )
    if not isinstance(raw_action_arguments, dict):
        raise InvalidDiagnosisError("Diagnosis action_arguments must be an object.")

    hypotheses: list[DiagnosisHypothesis] = []
    for index, raw_hypothesis in enumerate(raw_hypotheses, start=1):
        if not isinstance(raw_hypothesis, Mapping):
            raise InvalidDiagnosisError("Each diagnosis hypothesis must be an object.")
        evidence = raw_hypothesis.get("supporting_evidence")
        if evidence is None:
            evidence = raw_hypothesis.get("evidence_refs")
        if not isinstance(evidence, list) or not all(
            isinstance(reference, str) for reference in evidence
        ):
            raise InvalidDiagnosisError("Each hypothesis requires evidence references.")
        confidence = raw_hypothesis.get("confidence", decision.confidence)
        try:
            hypotheses.append(
                DiagnosisHypothesis(
                    hypothesis_id=f"hypothesis-{index:03d}",
                    statement=_required_string(
                        raw_hypothesis.get("statement"), "statement"
                    ),
                    supporting_evidence=evidence,
                    confidence=confidence,
                )
            )
        except ValidationError as exc:
            raise InvalidDiagnosisError(
                "Diagnosis hypothesis failed validation."
            ) from exc

    try:
        return Diagnosis(
            failure_class=FailureClass(raw_failure_class),
            summary=decision.summary,
            hypotheses=hypotheses,
            supporting_evidence=[
                reference for reference in raw_supporting if isinstance(reference, str)
            ],
            recommended_action=DiagnosisAction(raw_action),
            action_arguments=raw_action_arguments,
            expected_observation=_required_string(raw_expected, "expected_observation"),
            confidence=decision.confidence,
            risk=DiagnosisRisk(raw_risk),
        )
    except (ValidationError, ValueError) as exc:
        raise InvalidDiagnosisError(
            "Provider diagnosis failed schema validation."
        ) from exc
