"""Deterministic construction of future objective verification contracts."""

from __future__ import annotations

from reproscout.planning import PlanActionType, ReproductionPlan

from .models import (
    VerificationContract,
    VerificationTarget,
    VerificationTargetType,
)


def contract_from_plan(plan: ReproductionPlan) -> VerificationContract:
    """Define evidence required for success without evaluating or classifying it."""

    targets: list[VerificationTarget] = []
    for step in plan.steps:
        if step.command is None:
            continue
        if step.action_type is PlanActionType.INSTALL_DEPENDENCY:
            target_type = VerificationTargetType.INSTALLATION_SUCCEEDS
        elif step.action_type is PlanActionType.RUN_TESTS:
            target_type = VerificationTargetType.TESTS_EXECUTE
        else:
            target_type = VerificationTargetType.COMMAND_EXITS_SUCCESSFULLY
        targets.append(
            VerificationTarget(
                target_id=f"verify-{len(targets) + 1:03d}",
                target_type=target_type,
                description=step.expected_outcome,
                command=(
                    step.command
                    if target_type
                    in {
                        VerificationTargetType.COMMAND_EXITS_SUCCESSFULLY,
                        VerificationTargetType.TESTS_EXECUTE,
                    }
                    else None
                ),
            )
        )
    if not targets:
        raise ValueError("A verification contract requires an executable plan step.")
    return VerificationContract(goal=plan.goal, targets=targets)
