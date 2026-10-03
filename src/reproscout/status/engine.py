"""Deterministic final-status policy over verification and safety evidence."""

from __future__ import annotations

from collections.abc import Iterable

from reproscout.verification import (
    GoalCoverage,
    VerificationLevel,
    VerificationResult,
    VerificationResultStatus,
)

from .models import (
    ReproductionStatus,
    ReproductionStatusResult,
    StatusReason,
    StatusReasonCode,
)


def compute_reproduction_status(
    verification: VerificationResult | None,
    *,
    workflow_succeeded: bool | None,
    clean_room_required: bool = False,
    clean_room_verified: bool | None = None,
    unsafe_findings: Iterable[str] = (),
) -> ReproductionStatusResult:
    """Compute one status using only deterministic evidence and policy inputs."""

    findings = [finding[:2_000] for finding in unsafe_findings if finding]
    if findings:
        reasons = [
            _reason(
                StatusReasonCode.SAFETY_POLICY_VIOLATION,
                finding,
                reason_id=f"reason-{index:03d}",
            )
            for index, finding in enumerate(findings, 1)
        ]
        return _result(
            ReproductionStatus.UNSAFE,
            reasons,
            verification,
            workflow_succeeded,
            clean_room_required,
            clean_room_verified,
        )
    if verification is None:
        return _result(
            ReproductionStatus.BLOCKED,
            [
                _reason(
                    StatusReasonCode.VERIFICATION_UNAVAILABLE,
                    "No verification result was supplied.",
                )
            ],
            verification,
            workflow_succeeded,
            clean_room_required,
            clean_room_verified,
        )
    refs = [check.check_id for check in verification.checks]
    if verification.status is VerificationResultStatus.EXECUTION_FAILED:
        return _result(
            ReproductionStatus.FAILED,
            [_reason(StatusReasonCode.EXECUTION_FAILED, verification.summary, refs)],
            verification,
            workflow_succeeded,
            clean_room_required,
            clean_room_verified,
        )
    if verification.status is VerificationResultStatus.FAILED:
        return _result(
            ReproductionStatus.FAILED,
            [
                _reason(
                    StatusReasonCode.OBJECTIVE_VERIFICATION_FAILED,
                    verification.summary,
                    refs,
                )
            ],
            verification,
            workflow_succeeded,
            clean_room_required,
            clean_room_verified,
        )
    if verification.status is VerificationResultStatus.UNAVAILABLE:
        return _result(
            ReproductionStatus.BLOCKED,
            [
                _reason(
                    StatusReasonCode.VERIFICATION_UNAVAILABLE,
                    verification.summary,
                    refs,
                )
            ],
            verification,
            workflow_succeeded,
            clean_room_required,
            clean_room_verified,
        )
    if verification.status is VerificationResultStatus.UNSPECIFIED:
        return _result(
            ReproductionStatus.BLOCKED,
            [
                _reason(
                    StatusReasonCode.VERIFICATION_UNSPECIFIED,
                    verification.summary,
                    refs,
                )
            ],
            verification,
            workflow_succeeded,
            clean_room_required,
            clean_room_verified,
        )
    if workflow_succeeded is not True:
        return _result(
            ReproductionStatus.PARTIAL,
            [
                _reason(
                    StatusReasonCode.WORKFLOW_NOT_CONFIRMED,
                    "Workflow success was not objectively confirmed.",
                    refs,
                )
            ],
            verification,
            workflow_succeeded,
            clean_room_required,
            clean_room_verified,
        )
    if (
        verification.goal_coverage is not None
        and verification.goal_coverage.coverage_status != "COMPLETE"
    ):
        return _result(
            ReproductionStatus.PARTIAL,
            [
                _reason(
                    StatusReasonCode.GOAL_NOT_COVERED,
                    "The requested goal has unmet execution milestones: "
                    + ", ".join(verification.goal_coverage.unmet_milestones),
                    [
                        observation.milestone
                        for observation in verification.goal_coverage.observations
                    ],
                )
            ],
            verification,
            workflow_succeeded,
            clean_room_required,
            clean_room_verified,
        )
    if verification.level in {VerificationLevel.L0, VerificationLevel.L1}:
        return _result(
            ReproductionStatus.PARTIAL,
            [
                _reason(
                    StatusReasonCode.VERIFICATION_LEVEL_INSUFFICIENT,
                    "Verification did not reach target execution level.",
                    refs,
                )
            ],
            verification,
            workflow_succeeded,
            clean_room_required,
            clean_room_verified,
        )
    if clean_room_required and clean_room_verified is not True:
        code = (
            StatusReasonCode.CLEAN_ROOM_FAILED
            if clean_room_verified is False
            else StatusReasonCode.CLEAN_ROOM_REQUIRED
        )
        return _result(
            ReproductionStatus.PARTIAL,
            [
                _reason(
                    code,
                    "A clean-room rerun is required before claiming reproduction.",
                    refs,
                )
            ],
            verification,
            workflow_succeeded,
            clean_room_required,
            clean_room_verified,
        )
    return _result(
        ReproductionStatus.REPRODUCED,
        [
            _reason(
                StatusReasonCode.OBJECTIVE_VERIFICATION_PASSED,
                verification.summary,
                refs,
            )
        ],
        verification,
        workflow_succeeded,
        clean_room_required,
        clean_room_verified,
        verification.goal_coverage if verification else None,
    )


def _reason(
    code: StatusReasonCode,
    detail: str,
    evidence_refs: list[str] | None = None,
    *,
    reason_id: str = "reason-001",
) -> StatusReason:
    return StatusReason(
        reason_id=reason_id,
        code=code,
        detail=detail,
        evidence_refs=evidence_refs or [],
    )


def _result(
    status: ReproductionStatus,
    reasons: list[StatusReason],
    verification: VerificationResult | None,
    workflow_succeeded: bool | None,
    clean_room_required: bool,
    clean_room_verified: bool | None,
    goal_coverage: GoalCoverage | None = None,
) -> ReproductionStatusResult:
    if goal_coverage is None and verification is not None:
        goal_coverage = verification.goal_coverage
    return ReproductionStatusResult(
        status=status,
        reasons=reasons,
        verification_status=verification.status if verification else None,
        verification_level=verification.level if verification else None,
        workflow_succeeded=workflow_succeeded,
        clean_room_required=clean_room_required,
        clean_room_verified=clean_room_verified,
        goal_coverage=goal_coverage,
    )
