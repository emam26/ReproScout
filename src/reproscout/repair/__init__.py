"""Controlled repair-action and experiment foundation."""

from .engine import RepairExperimentEngine
from .models import (
    RepairAction,
    RepairActionType,
    RepairExperimentResult,
    RepairExperimentStatus,
    RepairLimits,
    RepairObservation,
    RepairRisk,
    Reversibility,
)
from .pipeline import (
    ControlledRepairPipeline,
    ControlledRepairRunResult,
    RepairPipelineError,
)
from .plan import AppliedPlanRepair, PlanRepairApplier, RepairNotExecutableError
from .policy import RepairPolicyError, validate_repair_action
from .workspace import (
    AppliedWorkspaceRepair,
    FileDigest,
    WorkspaceChange,
    WorkspaceEditConflictError,
    WorkspaceEditError,
    WorkspaceEditLimits,
    WorkspaceEditor,
    WorkspaceEditResult,
    WorkspaceFile,
    WorkspaceRepairApplier,
)

__all__ = [
    "AppliedPlanRepair",
    "AppliedWorkspaceRepair",
    "ControlledRepairPipeline",
    "ControlledRepairRunResult",
    "FileDigest",
    "PlanRepairApplier",
    "RepairAction",
    "RepairActionType",
    "RepairExperimentEngine",
    "RepairExperimentResult",
    "RepairExperimentStatus",
    "RepairLimits",
    "RepairNotExecutableError",
    "RepairObservation",
    "RepairPipelineError",
    "RepairPolicyError",
    "RepairRisk",
    "Reversibility",
    "WorkspaceChange",
    "WorkspaceEditConflictError",
    "WorkspaceEditError",
    "WorkspaceEditLimits",
    "WorkspaceEditResult",
    "WorkspaceEditor",
    "WorkspaceFile",
    "WorkspaceRepairApplier",
    "validate_repair_action",
]
