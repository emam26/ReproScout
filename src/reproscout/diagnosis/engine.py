"""Bounded deterministic-first diagnosis orchestration."""

from __future__ import annotations

import re
from collections import Counter

from reproscout.diagnostics import DiagnosticContext, FailureClass
from reproscout.llm import LLMError, LLMProvider
from reproscout.state import AgentAction, Attempt, RunStore, Stage

from .models import (
    Diagnosis,
    DiagnosisAction,
    DiagnosisLimits,
    DiagnosisResult,
    DiagnosisRisk,
    DiagnosisStatus,
)
from .parser import InvalidDiagnosisError, parse_diagnosis_decision
from .policy import DiagnosisPolicyError, RepeatedDiagnosisError, validate_diagnosis
from .prompt import build_diagnosis_request


class DiagnosisEngineError(RuntimeError):
    """Raised only for invalid engine configuration, not provider failures."""


_MODULE_NAME = re.compile(r"no module named ['\"]?([A-Za-z0-9_.-]+)", re.IGNORECASE)


def _first_evidence(context: DiagnosticContext) -> list[str]:
    return [context.evidence[0].reference]


def deterministic_diagnosis(context: DiagnosticContext) -> Diagnosis | None:
    """Resolve only straightforward facts without consuming an LLM call."""

    failure_text = "\n".join(item.content for item in context.evidence)
    evidence = _first_evidence(context)
    if context.failure_class is FailureClass.TIMEOUT:
        return Diagnosis(
            failure_class=context.failure_class,
            summary="The attempted command exceeded its configured timeout.",
            hypotheses=[
                {
                    "hypothesis_id": "hypothesis-001",
                    "statement": "The command requires more time or is stalled.",
                    "supporting_evidence": evidence,
                    "confidence": 0.99,
                }
            ],
            supporting_evidence=evidence,
            recommended_action=DiagnosisAction.GATHER_MORE_EVIDENCE,
            expected_observation="A bounded inspection distinguishes slow progress from a stalled command.",
            confidence=0.99,
            risk=DiagnosisRisk.LOW,
        )
    if context.failure_class is FailureClass.AUTH_REQUIRED:
        return Diagnosis(
            failure_class=context.failure_class,
            summary="The target operation requires credentials that are not available to the run.",
            hypotheses=[
                {
                    "hypothesis_id": "hypothesis-001",
                    "statement": "The external resource rejected unauthenticated access.",
                    "supporting_evidence": evidence,
                    "confidence": 0.98,
                }
            ],
            supporting_evidence=evidence,
            recommended_action=DiagnosisAction.STOP_UNREPAIRABLE,
            expected_observation="No safe credential-free retry is expected to succeed.",
            confidence=0.98,
            risk=DiagnosisRisk.MEDIUM,
        )
    if context.failure_class is FailureClass.MISSING_PACKAGE:
        match = _MODULE_NAME.search(failure_text)
        if match:
            module = match.group(1).split(".", 1)[0]
            return Diagnosis(
                failure_class=context.failure_class,
                summary=f"The command imports missing module {module}.",
                hypotheses=[
                    {
                        "hypothesis_id": "hypothesis-001",
                        "statement": f"The isolated environment lacks a distribution providing {module}.",
                        "supporting_evidence": evidence,
                        "confidence": 0.9,
                    }
                ],
                supporting_evidence=evidence,
                recommended_action=DiagnosisAction.ADD_DEPENDENCY,
                action_arguments={"import_name": module},
                expected_observation="Dependency evidence identifies a declared or mapped distribution.",
                confidence=0.9,
                risk=DiagnosisRisk.MEDIUM,
            )
    return None


class DiagnosisSession:
    """One bounded diagnosis session; history is also persisted as audit events."""

    def __init__(
        self,
        store: RunStore,
        provider: LLMProvider,
        *,
        limits: DiagnosisLimits | None = None,
    ) -> None:
        self.store = store
        self.provider = provider
        self.limits = limits or DiagnosisLimits()
        self.attempts = 0
        self.llm_calls = 0
        self.failure_signatures: Counter[str] = Counter()
        self.recommendations: Counter[str] = Counter()

    def diagnose(
        self,
        context: DiagnosticContext,
        *,
        run_id: str | None = None,
    ) -> DiagnosisResult:
        """Return a policy-checked proposal or a bounded stop result."""

        if context.total_characters > self.limits.max_context_size:
            return self._stopped(
                "Diagnostic context exceeds the configured diagnosis bound.",
                run_id=run_id,
                context=context,
            )
        signature_count = self.failure_signatures[context.failure_signature]
        if signature_count >= self.limits.max_repeated_failure_signatures:
            return self._stopped(
                "Repeated failure signature exceeded the diagnosis limit.",
                run_id=run_id,
                context=context,
            )
        if self.attempts >= self.limits.max_diagnosis_attempts:
            return self._stopped(
                "Maximum diagnosis attempts reached.",
                run_id=run_id,
                context=context,
            )

        self.attempts += 1
        self.failure_signatures[context.failure_signature] += 1
        self._record_start(run_id, context)

        deterministic = deterministic_diagnosis(context)
        if deterministic is not None:
            try:
                self._validate_and_count(deterministic, context)
            except RepeatedDiagnosisError as exc:
                return self._stopped(str(exc), run_id=run_id, context=context)
            except DiagnosisPolicyError as exc:
                return self._policy_rejected(str(exc), run_id=run_id, context=context)
            result = DiagnosisResult(
                status=DiagnosisStatus.DETERMINISTIC,
                diagnosis=deterministic,
            )
            self._record_result(run_id, context, result)
            return result

        if self.llm_calls >= self.limits.max_llm_calls:
            return self._stopped(
                "Maximum LLM diagnosis calls reached.",
                run_id=run_id,
                context=context,
            )
        self.llm_calls += 1
        request = build_diagnosis_request(
            context,
            max_output_tokens=min(1_024, self.limits.max_context_size // 4),
        )
        try:
            response = _run_async(self.provider.decide(request))
            diagnosis = parse_diagnosis_decision(response.decision, context)
            self._validate_and_count(diagnosis, context)
        except RepeatedDiagnosisError as exc:
            return self._stopped(str(exc), run_id=run_id, context=context)
        except (LLMError, InvalidDiagnosisError, DiagnosisPolicyError) as exc:
            if isinstance(exc, DiagnosisPolicyError):
                result = self._policy_rejected(str(exc), run_id=run_id, context=context)
            elif isinstance(exc, InvalidDiagnosisError):
                result = self._policy_rejected(
                    f"Provider diagnosis schema rejected: {exc}",
                    run_id=run_id,
                    context=context,
                )
            else:
                result = DiagnosisResult(
                    status=DiagnosisStatus.PROVIDER_ERROR,
                    provider=getattr(self.provider, "provider_name", "unknown"),
                    model=getattr(self.provider, "model", None),
                    stop_reason="LLM provider request failed.",
                )
            self._record_result(run_id, context, result)
            return result

        result = DiagnosisResult(
            status=DiagnosisStatus.LLM,
            diagnosis=diagnosis,
            provider=response.provider,
            model=response.model,
            usage=response.usage,
        )
        self._record_result(run_id, context, result)
        return result

    def _validate_and_count(
        self, diagnosis: Diagnosis, context: DiagnosticContext
    ) -> None:
        validate_diagnosis(diagnosis, context, limits=self.limits)
        recommendation = diagnosis.recommended_action.value
        if (
            self.recommendations[recommendation]
            >= self.limits.max_repeated_recommendations
        ):
            raise RepeatedDiagnosisError(
                "Repeated diagnosis recommendation exceeded the limit."
            )
        self.recommendations[recommendation] += 1

    def _record_start(self, run_id: str | None, context: DiagnosticContext) -> None:
        if run_id is None:
            return
        self.store.record_attempt(
            run_id,
            Attempt.create(
                "diagnosis",
                Stage.DEBUG,
                {
                    "failure_class": context.failure_class.value,
                    "failure_signature": context.failure_signature,
                    "evidence_count": len(context.evidence),
                    "estimated_tokens": context.estimated_tokens,
                },
            ),
        )
        self.store.record_agent_action(
            run_id,
            AgentAction.create(
                "diagnosis_started",
                {
                    "failure_signature": context.failure_signature,
                    "provider": getattr(self.provider, "provider_name", "unknown"),
                    "model": getattr(self.provider, "model", None) or "unknown",
                    "evidence_count": len(context.evidence),
                },
            ),
        )

    def _record_result(
        self,
        run_id: str | None,
        context: DiagnosticContext,
        result: DiagnosisResult,
    ) -> None:
        if run_id is None:
            return
        payload: dict[str, object] = {
            "failure_signature": context.failure_signature,
            "status": result.status.value,
            "provider": result.provider,
            "model": result.model,
            "policy_rejection": result.policy_rejection,
            "stop_reason": result.stop_reason,
        }
        if result.diagnosis is not None:
            payload["diagnosis"] = result.diagnosis.model_dump(mode="json")
        if result.usage is not None:
            payload["usage"] = result.usage.model_dump(mode="json")
        self.store.record_agent_action(
            run_id,
            AgentAction.create("diagnosis_result", payload),
        )

    def _policy_rejected(
        self,
        reason: str,
        *,
        run_id: str | None,
        context: DiagnosticContext,
    ) -> DiagnosisResult:
        return DiagnosisResult(
            status=DiagnosisStatus.POLICY_REJECTED,
            policy_rejection=reason[:1_000],
        )

    def _stopped(
        self,
        reason: str,
        *,
        run_id: str | None,
        context: DiagnosticContext,
    ) -> DiagnosisResult:
        result = DiagnosisResult(
            status=DiagnosisStatus.STOPPED, stop_reason=reason[:1_000]
        )
        self._record_result(run_id, context, result)
        return result


def _run_async(awaitable):
    """Run the async provider from the synchronous control-plane API."""

    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    raise DiagnosisEngineError(
        "DiagnosisSession cannot run inside an active event loop."
    )
