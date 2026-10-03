"""Fresh-workspace, fresh-container clean-room reproduction."""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

from reproscout.diagnostics import contract_from_plan
from reproscout.execution import PlanExecutionEngine
from reproscout.planning import ReproductionPlan
from reproscout.planning.safety import PlanSafetyError, validate_command
from reproscout.repair import WorkspaceEditor
from reproscout.security import redact_sensitive_text
from reproscout.state import RunStore, Stage
from reproscout.status import compute_reproduction_status
from reproscout.verification import ObjectiveVerificationEngine

from .models import CleanRoomLimits, CleanRoomRecipe, CleanRoomResult


class CleanRoomError(RuntimeError):
    """Raised when a clean-room recipe cannot be applied safely."""


def recipe_from_plan(
    plan: ReproductionPlan,
    *,
    patches_diff: str | None = None,
) -> CleanRoomRecipe:
    """Derive a portable recipe from real plan commands and optional diff data."""

    commands = [step.command for step in plan.steps if step.command is not None]
    if not commands:
        raise CleanRoomError("A clean-room recipe requires at least one command.")
    for command in commands:
        assert command is not None
        try:
            validate_command(command, max_length=2_000)
        except PlanSafetyError as exc:
            raise CleanRoomError(str(exc)) from exc
    if patches_diff is not None and not patches_diff:
        patches_diff = None
    if patches_diff is not None and len(patches_diff) > 1_000_000:
        raise CleanRoomError("Clean-room patch exceeds its configured bound.")
    return CleanRoomRecipe(
        repository=plan.repository,
        commit_sha=plan.commit_sha,
        goal=plan.goal,
        plan=plan,
        recipe_commands=commands,
        patches_diff=patches_diff,
    )


class CleanRoomRunner:
    """Run a recipe in a newly copied workspace and newly created sandbox."""

    def __init__(
        self,
        store: RunStore,
        execution: PlanExecutionEngine,
        *,
        limits: CleanRoomLimits | None = None,
    ) -> None:
        self.store = store
        self.execution = execution
        self.limits = limits or CleanRoomLimits()

    def run(
        self,
        recipe: CleanRoomRecipe,
        *,
        source_workspace: Path,
        run_directory: Path,
    ) -> CleanRoomResult:
        source = Path(source_workspace).expanduser()
        if source.is_symlink() or not source.is_dir():
            raise CleanRoomError(
                "Clean-room source workspace must be a real directory."
            )
        root = Path(run_directory).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        clean_run_id = f"clean-room-{uuid.uuid4().hex}"
        clean_run_directory = root / clean_run_id
        clean_workspace = clean_run_directory / "workspace"
        clean_workspace.mkdir(parents=True)
        self._copy_workspace(source.resolve(), clean_workspace)
        if recipe.patches_diff is not None:
            editor = WorkspaceEditor(
                clean_workspace,
                limits=None,
                audit_directory=clean_run_directory,
            )
            if len(recipe.patches_diff) > self.limits.max_patch_characters:
                raise CleanRoomError("Clean-room patch exceeds its configured bound.")
            try:
                editor.apply_patch(recipe.patches_diff)
                editor.write_patch_artifact()
            except Exception as exc:
                raise CleanRoomError(
                    "Could not apply the clean-room recipe patch."
                ) from exc
        self._write_recipe_artifacts(clean_run_directory, recipe)
        run = self.store.create_run(
            run_id=clean_run_id,
            context={
                "repository": recipe.repository,
                "commit_sha": recipe.commit_sha,
                "clean_room": True,
            },
        )
        self.store.transition(run.run_id, Stage.ANALYZE)
        self.store.transition(run.run_id, Stage.PLAN)
        execution = self.execution.execute(
            recipe.plan,
            run_id=run.run_id,
            run_directory=clean_run_directory,
            workspace=clean_workspace,
        )
        verification = ObjectiveVerificationEngine().verify(
            contract_from_plan(recipe.plan),
            execution=execution,
            workspace=clean_workspace,
        )
        clean_room_verified = (
            execution.workflow_succeeded and verification.status.value == "PASSED"
        )
        status = compute_reproduction_status(
            verification,
            workflow_succeeded=execution.workflow_succeeded,
            clean_room_required=True,
            clean_room_verified=clean_room_verified,
        )
        return CleanRoomResult(
            clean_run_id=run.run_id,
            clean_workspace=str(clean_workspace),
            execution=execution,
            verification=verification,
            status=status,
        )

    @staticmethod
    def _write_recipe_artifacts(
        clean_run_directory: Path,
        recipe: CleanRoomRecipe,
    ) -> None:
        commands: list[str] = []
        for command in recipe.recipe_commands:
            if redact_sensitive_text(command) != command:
                raise CleanRoomError(
                    "Clean-room recipe contains credential-like material."
                )
            commands.append(command)
        (clean_run_directory / "reproduce.sh").write_text(
            "#!/usr/bin/env bash\nset -eu\n\n" + "\n".join(commands) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        (clean_run_directory / "REPRODUCTION.md").write_text(
            "# Clean-room reproduction\n\n"
            f"Repository: `{recipe.repository}`\n\n"
            f"Commit: `{recipe.commit_sha}`\n\n"
            "This recipe was applied in a newly copied workspace and a newly "
            "created Docker sandbox.\n\n"
            "## Commands\n\n"
            + "\n".join(f"- `{command}`" for command in commands)
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

    def _copy_workspace(self, source: Path, destination: Path) -> None:
        file_count = 0
        total_bytes = 0
        for current, directories, files in os.walk(
            source, topdown=True, followlinks=False
        ):
            current_path = Path(current)
            for directory in directories:
                if (current_path / directory).is_symlink():
                    raise CleanRoomError(
                        "Symlinked workspace directories are not copied."
                    )
            relative_dir = current_path.relative_to(source)
            target_dir = destination / relative_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            for filename in files:
                source_file = current_path / filename
                if source_file.is_symlink() or not source_file.is_file():
                    raise CleanRoomError(
                        "Symlinked or non-regular workspace files are not copied."
                    )
                try:
                    size = source_file.stat().st_size
                except OSError as exc:
                    raise CleanRoomError(
                        "Could not inspect a source workspace file."
                    ) from exc
                file_count += 1
                total_bytes += size
                if file_count > self.limits.max_files:
                    raise CleanRoomError(
                        "Source workspace exceeds the clean-room file limit."
                    )
                if total_bytes > self.limits.max_bytes:
                    raise CleanRoomError(
                        "Source workspace exceeds the clean-room byte limit."
                    )
                target = destination / source_file.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copyfile(source_file, target)
                except OSError as exc:
                    raise CleanRoomError(
                        "Could not copy source workspace file."
                    ) from exc
