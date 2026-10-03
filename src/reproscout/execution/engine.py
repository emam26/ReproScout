"""Sequential Docker-only execution of a finite reproduction plan."""

from __future__ import annotations

import shlex
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from reproscout.planning import PlanActionType, PlanStep, ReproductionPlan
from reproscout.sandbox import DockerSandbox, ExecutionResult, Sandbox, SandboxConfig
from reproscout.sandbox.base import SandboxError
from reproscout.security import redact_sensitive_text
from reproscout.state import (
    Attempt,
    RunOutcome,
    RunStore,
    Stage,
    ToolCall,
    ToolResult,
)

from .artifacts import RunArtifacts
from .models import (
    ExecutionFailureKind,
    ExecutionLimits,
    ExecutionRunResult,
    StepExecutionResult,
)


class ExecutionEngineError(RuntimeError):
    """Raised when a plan cannot be executed within the Phase 7 contract."""


SandboxFactory = Callable[[SandboxConfig], Sandbox]


_SETUP_ACTIONS = {
    PlanActionType.PREPARE_ENVIRONMENT,
    PlanActionType.INSTALL_DEPENDENCY,
    PlanActionType.PREPARE_ASSET,
    PlanActionType.RUN_SETUP,
}
_NETWORK_FAILURE_MARKERS = (
    "connection refused",
    "connection reset",
    "could not resolve",
    "name or service not known",
    "network is unreachable",
    "temporary failure in name resolution",
    "download failed",
)


def _default_sandbox_factory(config: SandboxConfig) -> Sandbox:
    return DockerSandbox(config)


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    marker = f"\n...[truncated {len(value) - limit} characters]"
    retained = max(0, limit - len(marker))
    return value[:retained] + marker


def _execution_command(step: PlanStep) -> str:
    if step.command is None:
        raise ExecutionEngineError("Commandless plan steps cannot be executed.")
    working = PurePosixPath(step.working_directory.replace("\\", "/"))
    if str(working) in {"", "."}:
        return step.command
    sandbox_path = PurePosixPath("/workspace") / working
    return f"cd -- {shlex.quote(str(sandbox_path))} && {step.command}"


def _classify_failure(
    step: PlanStep, result: ExecutionResult
) -> ExecutionFailureKind | None:
    if result.timed_out:
        return ExecutionFailureKind.TIMEOUT
    if result.exit_code == 0:
        return None
    combined = f"{result.stdout}\n{result.stderr}".lower()
    if result.exit_code in {137, 143} or "out of memory" in combined:
        return ExecutionFailureKind.RESOURCE_LIMIT_FAILURE
    if any(marker in combined for marker in _NETWORK_FAILURE_MARKERS):
        return ExecutionFailureKind.NETWORK_FAILURE
    if step.action_type is PlanActionType.INSTALL_DEPENDENCY:
        return ExecutionFailureKind.DEPENDENCY_INSTALLATION_FAILURE
    return ExecutionFailureKind.COMMAND_NONZERO


class PlanExecutionEngine:
    """Execute plan commands only through the existing sandbox abstraction."""

    def __init__(
        self,
        store: RunStore,
        *,
        sandbox_factory: SandboxFactory | None = None,
        limits: ExecutionLimits | None = None,
    ) -> None:
        self.store = store
        self.sandbox_factory = sandbox_factory or _default_sandbox_factory
        self.limits = limits or ExecutionLimits()

    def execute(
        self,
        plan: ReproductionPlan,
        *,
        run_id: str,
        run_directory: Path,
        workspace: Path,
        allow_repair: bool = False,
        finalize: bool = True,
    ) -> ExecutionRunResult:
        state = self.store.get_run(run_id)
        if state.stage not in {Stage.PLAN, Stage.DEBUG}:
            raise ExecutionEngineError(
                f"Execution requires PLAN or DEBUG stage; run is at {state.stage.value}."
            )
        artifacts = RunArtifacts.create(run_directory, workspace)
        started_at = datetime.now(UTC)
        started_monotonic = time.monotonic()
        if state.stage is Stage.PLAN:
            self.store.transition(run_id, Stage.SETUP)
            self.store.transition(run_id, Stage.EXECUTE)
        else:
            self.store.transition(run_id, Stage.EXECUTE)
        step_results: list[StepExecutionResult] = []
        failure_kind: ExecutionFailureKind | None = None
        failed_step_id: str | None = None

        command_steps = [step for step in plan.steps if step.command is not None]
        sandbox: Sandbox | None = None
        if command_steps:
            try:
                sandbox = self.sandbox_factory(
                    SandboxConfig(
                        workspace_path=Path(workspace),
                        run_id=run_id,
                        network=(
                            "bridge"
                            if any(step.network_required for step in command_steps)
                            else "none"
                        ),
                    )
                )
                sandbox.create()
            except SandboxError:
                failure_kind = ExecutionFailureKind.SANDBOX_FAILURE

        try:
            if failure_kind is None:
                for attempt_number, step in enumerate(plan.steps, start=1):
                    if step.command is None:
                        result = self._record_unavailable_prerequisite(
                            run_id,
                            step,
                            attempt_number,
                        )
                    else:
                        if sandbox is None:
                            raise AssertionError("Executable plan requires a sandbox.")
                        elapsed = time.monotonic() - started_monotonic
                        remaining = plan.overall_timeout_seconds - elapsed
                        if remaining <= 0:
                            result = self._record_budget_timeout(
                                run_id, step, attempt_number
                            )
                        else:
                            result = self._execute_step(
                                sandbox,
                                run_id,
                                step,
                                attempt_number,
                                timeout_seconds=min(step.timeout_seconds, remaining),
                                artifacts=artifacts,
                            )
                    if step.command is None:
                        artifacts.append_step(
                            result,
                            setup=step.action_type in _SETUP_ACTIONS,
                        )
                    step_results.append(result)
                    if result.failure_kind is not None:
                        failure_kind = result.failure_kind
                        failed_step_id = step.step_id
                        break
        finally:
            if sandbox is not None:
                try:
                    sandbox.destroy()
                except SandboxError:
                    if failure_kind is None:
                        failure_kind = ExecutionFailureKind.SANDBOX_FAILURE
                        failed_step_id = (
                            step_results[-1].step_id if step_results else None
                        )

        if not finalize or (failure_kind is not None and allow_repair):
            self.store.transition(run_id, Stage.DEBUG)
        else:
            self.store.transition(run_id, Stage.VERIFY)
            self.store.transition(run_id, Stage.REPORT)
            outcome = (
                RunOutcome.SUCCEEDED if failure_kind is None else RunOutcome.FAILED
            )
            self.store.finish(run_id, outcome)
        artifacts.write_events(self.store.list_events(run_id))
        finished_at = datetime.now(UTC)
        return ExecutionRunResult(
            run_id=run_id,
            workflow_succeeded=failure_kind is None,
            failure_kind=failure_kind,
            failed_step_id=failed_step_id,
            steps=step_results,
            started_at=started_at,
            finished_at=finished_at,
            artifacts=artifacts.paths,
        )

    def _record_unavailable_prerequisite(
        self,
        run_id: str,
        step: PlanStep,
        attempt_number: int,
    ) -> StepExecutionResult:
        timestamp = datetime.now(UTC)
        attempt = Attempt.create(
            "plan_prerequisite",
            Stage.EXECUTE,
            {
                "action_type": step.action_type.value,
                "attempt_number": attempt_number,
                "step_id": step.step_id,
            },
        )
        self.store.record_attempt(run_id, attempt)
        return StepExecutionResult(
            step_id=step.step_id,
            attempt_number=attempt_number,
            action_type=step.action_type.value,
            command=None,
            working_directory=step.working_directory,
            started_at=timestamp,
            finished_at=timestamp,
            duration=0,
            stdout="",
            stderr="Prerequisite cannot be prepared automatically in Phase 7.",
            exit_code=None,
            timed_out=False,
            failure_kind=ExecutionFailureKind.PREREQUISITE_UNAVAILABLE,
        )

    def _record_budget_timeout(
        self,
        run_id: str,
        step: PlanStep,
        attempt_number: int,
    ) -> StepExecutionResult:
        timestamp = datetime.now(UTC)
        attempt = Attempt.create(
            "plan_execution",
            Stage.EXECUTE,
            {
                "action_type": step.action_type.value,
                "attempt_number": attempt_number,
                "step_id": step.step_id,
            },
        )
        self.store.record_attempt(run_id, attempt)
        return StepExecutionResult(
            step_id=step.step_id,
            attempt_number=attempt_number,
            action_type=step.action_type.value,
            command=step.command,
            working_directory=step.working_directory,
            started_at=timestamp,
            finished_at=timestamp,
            duration=0,
            stdout="",
            stderr="Overall execution budget exhausted before this step.",
            exit_code=124,
            timed_out=True,
            failure_kind=ExecutionFailureKind.TIMEOUT,
        )

    def _execute_step(
        self,
        sandbox: Sandbox,
        run_id: str,
        step: PlanStep,
        attempt_number: int,
        *,
        timeout_seconds: float,
        artifacts: RunArtifacts,
    ) -> StepExecutionResult:
        attempt = Attempt.create(
            "plan_execution",
            Stage.EXECUTE,
            {
                "action_type": step.action_type.value,
                "attempt_number": attempt_number,
                "step_id": step.step_id,
            },
        )
        self.store.record_attempt(run_id, attempt)
        network_mode = getattr(getattr(sandbox, "config", None), "network", None)
        tool_call = ToolCall.create(
            "docker_execute",
            {
                "command": step.command,
                "step_id": step.step_id,
                "timeout_seconds": timeout_seconds,
                "working_directory": step.working_directory,
                "network_mode": network_mode,
            },
        )
        self.store.record_tool_call(run_id, tool_call)
        started_at = datetime.now(UTC)
        try:
            execution = sandbox.execute(
                _execution_command(step),
                timeout_seconds=timeout_seconds,
            )
            failure = _classify_failure(step, execution)
            stdout_for_log = _truncate(
                redact_sensitive_text(execution.stdout),
                self.limits.max_log_output_chars,
            )
            stderr_for_log = _truncate(
                redact_sensitive_text(execution.stderr),
                self.limits.max_log_output_chars,
            )
            stdout = _truncate(
                stdout_for_log,
                self.limits.max_persisted_output_chars,
            )
            stderr = _truncate(
                stderr_for_log,
                self.limits.max_persisted_output_chars,
            )
            finished_at = datetime.now(UTC)
            result = StepExecutionResult(
                step_id=step.step_id,
                attempt_number=attempt_number,
                action_type=step.action_type.value,
                command=step.command,
                working_directory=step.working_directory,
                started_at=started_at,
                finished_at=finished_at,
                duration=execution.duration,
                stdout=stdout,
                stderr=stderr,
                exit_code=execution.exit_code,
                timed_out=execution.timed_out,
                container_id=execution.container_id,
                failure_kind=failure,
            )
        except SandboxError:
            finished_at = datetime.now(UTC)
            stdout_for_log = ""
            stderr_for_log = "Sandbox execution failed."
            result = StepExecutionResult(
                step_id=step.step_id,
                attempt_number=attempt_number,
                action_type=step.action_type.value,
                command=step.command,
                working_directory=step.working_directory,
                started_at=started_at,
                finished_at=finished_at,
                duration=(finished_at - started_at).total_seconds(),
                stdout="",
                stderr=stderr_for_log,
                exit_code=None,
                timed_out=False,
                failure_kind=ExecutionFailureKind.SANDBOX_FAILURE,
            )
        tool_result = ToolResult.create(
            tool_call.call_id,
            succeeded=result.failure_kind is None,
            payload={
                "container_id": result.container_id,
                "duration": result.duration,
                "exit_code": result.exit_code,
                "failure_kind": (
                    result.failure_kind.value
                    if result.failure_kind is not None
                    else None
                ),
                "stderr": result.stderr,
                "stdout": result.stdout,
                "timed_out": result.timed_out,
                "network_mode": network_mode,
            },
        )
        self.store.record_tool_result(run_id, tool_result)
        artifacts.append_step(
            result,
            setup=step.action_type in _SETUP_ACTIONS,
            log_stdout=stdout_for_log,
            log_stderr=stderr_for_log,
        )
        return result
