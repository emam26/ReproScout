"""Environment-only preflight planning, evaluation, and artifacts."""

from .detector import inspect_environment
from .evaluator import evaluate_preflight
from .models import (
    PreflightArtifactPaths,
    PreflightAssessment,
    PreflightCommandResult,
    PreflightExecutionProfile,
    PreflightInspection,
    PreflightProfile,
    PreflightRequirement,
    PreflightResult,
    PreflightStatus,
    RequirementKind,
    RequirementState,
    resolve_preflight_profile,
)
from .planner import PreflightPlanner, PreflightPlanningError
from .writer import PreflightArtifactWriter

__all__ = [
    "PreflightArtifactPaths",
    "PreflightArtifactWriter",
    "PreflightAssessment",
    "PreflightCommandResult",
    "PreflightExecutionProfile",
    "PreflightInspection",
    "PreflightPlanner",
    "PreflightPlanningError",
    "PreflightProfile",
    "PreflightRequirement",
    "PreflightResult",
    "PreflightStatus",
    "RequirementKind",
    "RequirementState",
    "evaluate_preflight",
    "inspect_environment",
    "resolve_preflight_profile",
]
