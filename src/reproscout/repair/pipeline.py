"""A narrow execution-to-repair-to-retry orchestration boundary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from reproscout.diagnostics import failure_signature, normalize_failure
from reproscout.diagnostics.models import DiagnosticContext
from reproscout.execution import ExecutionRunResult, PlanExecutionEngine
from reproscout.planning import ReproductionPlan
from reproscout.state import RunStore, Stage

from .engine import RepairExperimentEngine
from .models import (
    RepairAction,
    RepairExperimentResult,
    RepairLimits,
    RepairObservation,
)
from .workspace import WorkspaceEditLimits, WorkspaceEditor, WorkspaceRepairApplier


class RepairPipelineError(RuntimeError):
    """Raised when a controlled retry cannot be placed in the run lifecycle."""


DiagnosisCallback = Callable[
    [ExecutionRunResult], tuple[DiagnosticContext, RepairAction | None]
]


@dataclass(frozen=True, slots=True)
class ControlledRepairRunResult:
    """Initial execution, optional repair experiment, and optional retry evidence."""

    initial: ExecutionRunResult
    repair: RepairExperimentResult | None
    retry: ExecutionRunResult | None


class ControlledRepairPipeline:
    """Connect one failed plan attempt to one bounded workspace retry.

    The pipeline deliberately returns an experiment result, not a final
    reproducibility status. A successful observation is rolled back and must
    later pass the formal verifier and clean-room flow.
    """

    def __init__(
        self,
        store: RunStore,
        execution: PlanExecutionEngine,
        *,
        editor_limits: WorkspaceEditLimits | None = None,
        repair_limits: RepairLimits | None = None,
    ) -> None:
        self.store = store
        self.execution = execution
        self.editor_limits = editor_limits
        self.repair_limits = repair_limits

    def run(
        self,
        plan: ReproductionPlan,
        *,
        run_id: str,
        run_directory: Path,
        workspace: Path,
        diagnose: DiagnosisCallback,
    ) -> ControlledRepairRunResult:
        initial = self.execution.execute(
            plan,
            run_id=run_id,
            run_directory=run_directory,
            workspace=workspace,
            allow_repair=True,
        )
        if initial.workflow_succeeded:
            return ControlledRepairRunResult(initial=initial, repair=None, retry=None)
        if self.store.get_run(run_id).stage is not Stage.DEBUG:
            raise RepairPipelineError("A repairable failed attempt must end at DEBUG.")

        context, action = diagnose(initial)
        if action is None:
            return ControlledRepairRunResult(initial=initial, repair=None, retry=None)
        editor = WorkspaceEditor(
            workspace,
            limits=self.editor_limits,
            audit_directory=run_directory,
        )
        backend = WorkspaceRepairApplier(editor)
        engine = RepairExperimentEngine(
            self.store,
            backend,
            limits=self.repair_limits,
        )
        retry_holder: list[ExecutionRunResult] = []

        def observe(_: object) -> RepairObservation:
            retry = self.execution.execute(
                plan,
                run_id=run_id,
                run_directory=run_directory,
                workspace=workspace,
                finalize=False,
            )
            retry_holder.append(retry)
            return self._observation(retry)

        repair = engine.run(
            action,
            context,
            before_failure_signature=context.failure_signature,
            observe=observe,
            run_id=run_id,
        )
        return ControlledRepairRunResult(
            initial=initial,
            repair=repair,
            retry=retry_holder[0] if retry_holder else None,
        )

    @staticmethod
    def _observation(result: ExecutionRunResult) -> RepairObservation:
        if result.workflow_succeeded:
            return RepairObservation(
                workflow_succeeded=True,
                summary="The repaired retry completed successfully in the sandbox.",
            )
        failed = next(
            (step for step in result.steps if step.failure_kind is not None),
            result.steps[-1] if result.steps else None,
        )
        if failed is None:
            return RepairObservation(
                workflow_succeeded=False,
                summary="The repaired retry produced no executable step evidence.",
            )
        normalized = normalize_failure(
            failed.stdout,
            failed.stderr,
            exit_code=failed.exit_code,
            timed_out=failed.timed_out,
        )
        return RepairObservation(
            workflow_succeeded=False,
            failure_class=normalized.failure_class,
            failure_signature=failure_signature(
                normalized,
                command=failed.command or "",
            ),
            exit_code=failed.exit_code,
            summary=normalized.headline,
        )
