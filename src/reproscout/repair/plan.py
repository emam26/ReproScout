"""Pure plan transformations for the Phase 9 experiment backend."""

from __future__ import annotations

from dataclasses import dataclass

from reproscout.planning import PlanActionType, PlanStep, ReproductionPlan
from reproscout.planning.safety import PlanSafetyError, validate_command

from .models import RepairAction, RepairActionType
from .policy import RepairPolicyError, _require_requirement


class RepairNotExecutableError(RepairPolicyError):
    """Raised when an action requires a later Phase 10 tool."""


@dataclass(frozen=True, slots=True)
class AppliedPlanRepair:
    """Repaired immutable plan plus its exact pre-repair version."""

    before: ReproductionPlan
    after: ReproductionPlan

    def rollback(self) -> ReproductionPlan:
        return self.before


def _renumber(steps: list[PlanStep]) -> list[PlanStep]:
    return [
        step.model_copy(update={"step_id": f"step-{index:03d}"})
        for index, step in enumerate(steps, 1)
    ]


class PlanRepairApplier:
    """Apply only pure plan-level invocation/dependency changes."""

    def apply(self, plan: ReproductionPlan, action: RepairAction) -> AppliedPlanRepair:
        if action.action_type is RepairActionType.CHANGE_INVOCATION:
            return self._change_invocation(plan, action)
        if action.action_type in {
            RepairActionType.ADD_DEPENDENCY,
            RepairActionType.CHANGE_DEPENDENCY_VERSION,
        }:
            return self._change_dependency(plan, action)
        raise RepairNotExecutableError(
            f"{action.action_type.value} requires a later controlled tool."
        )

    def _change_invocation(
        self,
        plan: ReproductionPlan,
        action: RepairAction,
    ) -> AppliedPlanRepair:
        step_id = action.arguments["step_id"]
        command = action.arguments["command"]
        assert isinstance(step_id, str) and isinstance(command, str)
        steps: list[PlanStep] = []
        changed = False
        for step in plan.steps:
            if step.step_id == step_id:
                steps.append(step.model_copy(update={"command": command}))
                changed = True
            else:
                steps.append(step)
        if not changed:
            raise RepairPolicyError(f"Unknown plan step: {step_id}.")
        return AppliedPlanRepair(
            before=plan, after=plan.model_copy(update={"steps": steps})
        )

    def _change_dependency(
        self,
        plan: ReproductionPlan,
        action: RepairAction,
    ) -> AppliedPlanRepair:
        requirement = _require_requirement(
            action.arguments.get("requirement"), field="requirement"
        )
        requested_step = action.arguments.get("step_id")
        target_index = next(
            (
                index
                for index, step in enumerate(plan.steps)
                if step.action_type is PlanActionType.INSTALL_DEPENDENCY
                and (requested_step is None or step.step_id == requested_step)
            ),
            None,
        )
        if target_index is None:
            raise RepairNotExecutableError(
                "No dependency-installation step is available."
            )
        target = plan.steps[target_index]
        command = target.command or "python -m pip install ."
        if action.action_type is RepairActionType.CHANGE_DEPENDENCY_VERSION:
            command = f"{command} {requirement}"
        else:
            command = f"{command} {requirement}"
        try:
            validate_command(command, max_length=1_000)
        except PlanSafetyError as exc:
            raise RepairPolicyError(str(exc)) from exc
        steps = list(plan.steps)
        steps[target_index] = target.model_copy(update={"command": command})
        return AppliedPlanRepair(
            before=plan, after=plan.model_copy(update={"steps": steps})
        )
