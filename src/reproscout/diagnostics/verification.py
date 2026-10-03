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
            milestone = "install"
        elif step.action_type is PlanActionType.RUN_TESTS:
            target_type = VerificationTargetType.TESTS_EXECUTE
            milestone = "tests"
        elif step.action_type is PlanActionType.RUN_DEMO:
            target_type = VerificationTargetType.COMMAND_EXITS_SUCCESSFULLY
            milestone = "demo"
        else:
            target_type = VerificationTargetType.COMMAND_EXITS_SUCCESSFULLY
            milestone = None
        targets.append(
            VerificationTarget(
                target_id=f"verify-{len(targets) + 1:03d}",
                step_id=step.step_id,
                attempt_number=step.attempt_number,
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
                milestone=milestone,
            )
        )
    for milestone in plan.required_milestones:
        if not any(target.milestone == milestone for target in targets):
            targets.append(
                VerificationTarget(
                    target_id=f"verify-{len(targets) + 1:03d}",
                    target_type=VerificationTargetType.GOAL_MILESTONE,
                    milestone=milestone,
                    description=f"Required goal milestone {milestone} is executed.",
                )
            )
    if not targets:
        raise ValueError("A verification contract requires an executable plan step.")
    return VerificationContract(
        goal=plan.goal,
        repository=plan.repository,
        commit_sha=plan.commit_sha,
        required_milestones=plan.required_milestones,
        targets=targets,
    )
