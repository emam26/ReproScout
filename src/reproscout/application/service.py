"""Bounded application workflows shared by public interfaces.

This module is deliberately a thin composition layer. Repository code is only
executed by ``PlanExecutionEngine`` through the Docker sandbox; inspection and
reporting never execute target commands on the host.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from reproscout.analysis import RepositoryAnalysis, RepositoryAnalyzer
from reproscout.cleanroom import CleanRoomError, CleanRoomRunner, recipe_from_plan
from reproscout.config import get_settings
from reproscout.diagnosis import (
    DiagnosisResult,
    DiagnosisSession,
    deterministic_diagnosis,
)
from reproscout.diagnostics import EvidenceBuilder, normalize_failure
from reproscout.diagnostics.verification import contract_from_plan
from reproscout.execution import ExecutionRunResult, PlanExecutionEngine
from reproscout.llm import LLMProvider, create_provider, get_llm_settings
from reproscout.planning import ReproductionPlan, ReproductionPlanner
from reproscout.repo.clone import (
    CloneError,
    RunWorkspace,
    RunWorkspaceError,
    clone_repository,
    clone_repository_at_commit,
    create_run_workspace,
)
from reproscout.repo.manifest import ManifestError, RepositoryManifest, build_manifest
from reproscout.repo.urls import GitHubRepository, RepositoryUrlError, parse_github_url
from reproscout.reporting import (
    ReportAttempt,
    ReportAttemptSource,
    ReportFailure,
    ReportPlanStep,
    RunReport,
    RunReportWriter,
)
from reproscout.sandbox import SandboxError, check_docker_available
from reproscout.security import (
    BoundedReadError,
    read_bounded_workspace_text,
    redact_sensitive_text,
    reject_sensitive_mapping,
)
from reproscout.state import (
    AgentAction,
    EventType,
    RunOutcome,
    RunStore,
    SQLiteRunStore,
    Stage,
)
from reproscout.status import ReproductionStatusResult, compute_reproduction_status
from reproscout.verification import ObjectiveVerificationEngine, VerificationResult

Goal = Literal["auto", "install", "tests", "demo"]
_SUPPORTED_GOALS = frozenset({"auto", "install", "tests", "demo"})


class AuditRequest(BaseModel):
    """Validated bounded options for one local audit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    repository_url: str = Field(min_length=1, max_length=2_000)
    goal: Goal = "auto"
    no_ai: bool = True
    provider: str | None = Field(default=None, max_length=50)
    model: str | None = Field(default=None, max_length=200)

    @field_validator("goal", mode="before")
    @classmethod
    def _normalize_goal(cls, value: object) -> str:
        normalized = str(value).strip().lower()
        if normalized not in _SUPPORTED_GOALS:
            raise ValueError("goal must be one of: auto, install, tests, demo")
        return normalized


class AuditResult(BaseModel):
    """Machine-readable summary of a completed bounded audit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    repository: str
    commit_sha: str
    goal: str
    stage: Stage
    status: ReproductionStatusResult
    verification: VerificationResult | None = None
    attempts: int = Field(ge=0)
    repairs: int = Field(ge=0)
    report_path: Path
    reproduction_package_path: Path | None = None


class InspectionResult(BaseModel):
    """Deterministic inspection output without target execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    run_directory: Path
    manifest: RepositoryManifest
    analysis: RepositoryAnalysis


class ReproductionResult(BaseModel):
    """Result of replaying a persisted plan in a fresh Docker clean-room sandbox."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_run_id: str
    clean_run_id: str
    status: ReproductionStatusResult
    verification: VerificationResult
    package_path: Path


class ServiceError(RuntimeError):
    """Safe public error for application workflow failures."""


CloneFunction = Callable[[str, Path], object]
CleanRoomCloneFunction = Callable[[str, Path, str], object]
ManifestFunction = Callable[[GitHubRepository, object], RepositoryManifest]
ExecutionFactory = Callable[[RunStore], PlanExecutionEngine]


class AuditService:
    """Compose existing deterministic engines into a bounded user workflow."""

    def __init__(
        self,
        runs_dir: Path | None = None,
        *,
        clone_fn: CloneFunction = clone_repository,
        cleanroom_clone_fn: CleanRoomCloneFunction = clone_repository_at_commit,
        manifest_fn: ManifestFunction = build_manifest,
        analyzer: RepositoryAnalyzer | None = None,
        planner: ReproductionPlanner | None = None,
        execution_factory: ExecutionFactory | None = None,
    ) -> None:
        configured = runs_dir if runs_dir is not None else get_settings().runs_dir
        self.runs_dir = Path(configured).expanduser().resolve()
        self.clone_fn = clone_fn
        self.cleanroom_clone_fn = cleanroom_clone_fn
        self.manifest_fn = manifest_fn
        self.analyzer = analyzer or RepositoryAnalyzer()
        self.planner = planner or ReproductionPlanner()
        self.execution_factory = execution_factory or (
            lambda store: PlanExecutionEngine(store)
        )

    @property
    def database_path(self) -> Path:
        return self.runs_dir / "state.sqlite3"

    def inspect(self, request: AuditRequest) -> InspectionResult:
        """Clone and analyze a repository without executing its code."""

        repository, workspace = self._prepare_intake(request.repository_url)
        try:
            clone = self.clone_fn(repository.normalized_url, workspace.repository_path)
            manifest = self.manifest_fn(repository, clone)
            analysis = self._analyze(manifest, request)
        except (
            CloneError,
            ManifestError,
            RepositoryUrlError,
            RunWorkspaceError,
        ) as exc:
            raise ServiceError(f"Repository inspection failed: {exc}") from exc
        except Exception as exc:
            raise ServiceError("Repository inspection failed safely.") from exc
        return InspectionResult(
            run_id=workspace.run_id,
            run_directory=workspace.run_dir,
            manifest=manifest,
            analysis=analysis,
        )

    def audit(self, request: AuditRequest) -> AuditResult:
        """Run intake through objective verification and a clean-room rerun."""

        repository, workspace = self._prepare_intake(request.repository_url)
        with SQLiteRunStore(self.database_path) as store:
            run = store.create_run(
                run_id=workspace.run_id,
                context={
                    "repository_url": repository.normalized_url,
                    "goal": request.goal,
                    "ai_enabled": not request.no_ai,
                },
            )
            try:
                clone = self.clone_fn(
                    repository.normalized_url, workspace.repository_path
                )
                manifest = self.manifest_fn(repository, clone)
                store.record_agent_action(
                    run.run_id,
                    AgentAction.create(
                        "repository_intake_completed",
                        {
                            "repository": f"{repository.owner}/{repository.name}",
                            "commit_sha": manifest.commit_sha,
                        },
                    ),
                )
                store.transition(run.run_id, Stage.ANALYZE)
                analysis = self._analyze(manifest, request)
                store.record_agent_action(
                    run.run_id,
                    AgentAction.create(
                        "repository_analysis_completed",
                        {
                            "project_type": analysis.project_type,
                            "confidence": analysis.confidence,
                        },
                    ),
                )
                store.transition(run.run_id, Stage.PLAN)
                plan = self.planner.create_plan(analysis, goal=request.goal)
                store.record_agent_action(
                    run.run_id,
                    AgentAction.create(
                        "reproduction_plan_created",
                        {
                            "step_count": len(plan.steps),
                            "baseline": plan.baseline.value,
                        },
                    ),
                )
                self._write_plan(workspace.run_dir, plan)
                execution = self.execution_factory(store)
                execution_result = execution.execute(
                    plan,
                    run_id=run.run_id,
                    run_directory=workspace.run_dir,
                    workspace=manifest.workspace_path,
                    allow_repair=True,
                    finalize=False,
                )
                diagnosis_result = self._diagnose_if_needed(
                    store,
                    execution_result,
                    manifest,
                    provider=self._provider(request),
                )
                verification = self._verify(
                    plan, execution_result, manifest.workspace_path
                )
                clean_room_verified: bool | None = None
                package_path: Path | None = None
                recipe = None
                if execution_result.workflow_succeeded and verification is not None:
                    try:
                        recipe = recipe_from_plan(plan)
                        clean_result = CleanRoomRunner(
                            store,
                            execution,
                            clone_fn=self.cleanroom_clone_fn,
                        ).run(
                            recipe,
                            source_workspace=manifest.workspace_path,
                            run_directory=workspace.run_dir / "clean-room-runs",
                        )
                        clean_room_verified = (
                            clean_result.status.status.value == "REPRODUCED"
                        )
                        package_path = Path(clean_result.clean_workspace).parent
                    except (CleanRoomError, SandboxError, OSError):
                        clean_room_verified = False
                status = compute_reproduction_status(
                    verification,
                    workflow_succeeded=execution_result.workflow_succeeded,
                    clean_room_required=execution_result.workflow_succeeded,
                    clean_room_verified=clean_room_verified,
                )
                self._finish_after_verification(store, run.run_id, status)
                report = self._build_report(
                    request,
                    manifest,
                    plan,
                    execution_result,
                    verification,
                    status,
                    diagnosis_result,
                )
                artifact_paths = RunReportWriter(workspace.run_dir).write(
                    report,
                    events=store.list_events(run.run_id),
                    commands=execution_result.steps,
                    environment=(
                        execution_result.environment or plan.resolved_environment
                    ),
                    recipe_commands=recipe.recipe_commands
                    if recipe is not None
                    else None,
                )
                attempts = sum(
                    event.event_type is EventType.ATTEMPT_RECORDED
                    for event in store.list_events(run.run_id)
                )
                state = store.get_run(run.run_id)
                return AuditResult(
                    run_id=run.run_id,
                    repository=f"{manifest.owner}/{manifest.repository_name}",
                    commit_sha=manifest.commit_sha,
                    goal=request.goal,
                    stage=state.stage,
                    status=status,
                    verification=verification,
                    attempts=attempts,
                    repairs=0,
                    report_path=Path(artifact_paths.run_directory) / "report.md",
                    reproduction_package_path=package_path,
                )
            except (
                CloneError,
                ManifestError,
                RepositoryUrlError,
                RunWorkspaceError,
            ) as exc:
                store.append_event(
                    run.run_id,
                    EventType.AGENT_ACTION_RECORDED,
                    {
                        "action": "audit_failed",
                        "detail": redact_sensitive_text(str(exc))[:2_000],
                    },
                )
                raise ServiceError(f"Audit failed: {exc}") from exc
            except Exception as exc:
                store.append_event(
                    run.run_id,
                    EventType.AGENT_ACTION_RECORDED,
                    {
                        "action": "audit_failed",
                        "detail": "An internal bounded audit step failed.",
                    },
                )
                raise ServiceError(
                    "Audit failed safely; inspect the persisted run events."
                ) from exc

    def reproduce(self, run_id: str) -> ReproductionResult:
        """Replay a persisted plan through a fresh Docker clean-room run."""

        run_directory = self._run_directory(run_id)
        plan_path = run_directory / "plan.json"
        source_workspace = run_directory / "workspace" / "repository"
        if not plan_path.is_file() or not source_workspace.is_dir():
            raise ServiceError(
                "This run does not contain a persisted plan and source workspace."
            )
        try:
            plan = ReproductionPlan.model_validate(
                json.loads(plan_path.read_text(encoding="utf-8"))
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ServiceError("The persisted reproduction plan is invalid.") from exc
        try:
            with SQLiteRunStore(self.database_path) as store:
                store.get_run(run_id)
                execution = self.execution_factory(store)
                recipe = recipe_from_plan(plan)
                result = CleanRoomRunner(
                    store,
                    execution,
                    clone_fn=self.cleanroom_clone_fn,
                ).run(
                    recipe,
                    source_workspace=source_workspace.resolve(),
                    run_directory=run_directory / "reproduction-runs",
                )
        except (CleanRoomError, SandboxError, OSError) as exc:
            raise ServiceError(f"Clean-room reproduction failed: {exc}") from exc
        return ReproductionResult(
            source_run_id=run_id,
            clean_run_id=result.clean_run_id,
            status=result.status,
            verification=result.verification,
            package_path=Path(result.clean_workspace).parent,
        )

    def _prepare_intake(
        self, repository_url: str
    ) -> tuple[GitHubRepository, RunWorkspace]:
        try:
            repository = parse_github_url(repository_url)
            workspace = create_run_workspace(self.runs_dir)
        except (RepositoryUrlError, RunWorkspaceError) as exc:
            raise ServiceError(f"Repository intake failed: {exc}") from exc
        return repository, workspace

    def _run_directory(self, run_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", run_id):
            raise ServiceError("Run ID contains unsupported path characters.")
        root = self.runs_dir.resolve()
        candidate_input = root / run_id
        if candidate_input.is_symlink():
            raise ServiceError("Run directory cannot be a symlink.")
        candidate = candidate_input.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ServiceError("Run ID escapes the configured runs directory.") from exc
        if candidate.is_symlink() or not candidate.is_dir():
            raise ServiceError("Run directory was not found.")
        return candidate

    def _analyze(
        self,
        manifest: RepositoryManifest,
        request: AuditRequest,
    ) -> RepositoryAnalysis:
        return asyncio.run(
            self.analyzer.analyze(manifest, provider=self._provider(request))
        )

    def _provider(self, request: AuditRequest) -> LLMProvider | None:
        if request.no_ai:
            return None
        settings = get_llm_settings()
        updates: dict[str, object] = {}
        if request.provider is not None:
            updates["provider"] = request.provider
        if request.model is not None:
            updates["model"] = request.model
        if updates:
            settings = settings.model_copy(update=updates)
        return create_provider(settings)

    @staticmethod
    def _finish_after_verification(
        store: RunStore,
        run_id: str,
        status: ReproductionStatusResult,
    ) -> None:
        state = store.get_run(run_id)
        if state.stage is Stage.DEBUG:
            store.transition(run_id, Stage.VERIFY)
            store.transition(run_id, Stage.REPORT)
            outcome = (
                RunOutcome.SUCCEEDED
                if status.status.value == "REPRODUCED"
                else RunOutcome.FAILED
            )
            store.finish(run_id, outcome)

    @staticmethod
    def _verify(
        plan: ReproductionPlan,
        execution: ExecutionRunResult,
        workspace: Path,
    ) -> VerificationResult | None:
        try:
            contract = contract_from_plan(plan)
        except ValueError:
            return None
        return ObjectiveVerificationEngine().verify(
            contract,
            execution=execution,
            workspace=workspace,
        )

    @staticmethod
    def _diagnose_if_needed(
        store: RunStore,
        execution: ExecutionRunResult,
        manifest: RepositoryManifest,
        *,
        provider: LLMProvider | None,
    ) -> DiagnosisResult | None:
        failed = next(
            (step for step in execution.steps if step.failure_kind is not None), None
        )
        if failed is None:
            return None
        normalized = normalize_failure(
            failed.stdout,
            failed.stderr,
            exit_code=failed.exit_code,
            timed_out=failed.timed_out,
            log_reference="logs/execution.log",
        )
        documentation: list[tuple[str, str]] = []
        for relative in manifest.documentation_files[:5]:
            try:
                documentation.append(
                    (
                        relative,
                        read_bounded_workspace_text(
                            manifest.workspace_path,
                            relative,
                            max_bytes=8_000,
                        ),
                    )
                )
            except (BoundedReadError, OSError):
                continue
        context = EvidenceBuilder().build(
            normalized,
            command=failed.command or "",
            documentation=documentation,
        )
        if provider is not None:
            return DiagnosisSession(store, provider).diagnose(
                context, run_id=execution.run_id
            )
        deterministic = deterministic_diagnosis(context)
        if deterministic is None:
            return None
        return DiagnosisResult(status="DETERMINISTIC", diagnosis=deterministic)

    @staticmethod
    def _build_report(
        request: AuditRequest,
        manifest: RepositoryManifest,
        plan: ReproductionPlan,
        execution: ExecutionRunResult,
        verification: VerificationResult | None,
        status: ReproductionStatusResult,
        diagnosis: DiagnosisResult | None,
    ) -> RunReport:
        failures = []
        if execution.failure_kind is not None:
            failed = next(
                (step for step in execution.steps if step.failure_kind is not None),
                None,
            )
            if failed is not None:
                normalized = normalize_failure(
                    failed.stdout,
                    failed.stderr,
                    exit_code=failed.exit_code,
                    timed_out=failed.timed_out,
                )
                failures.append(
                    ReportFailure(
                        failure_id="failure-001",
                        failure_class=normalized.failure_class,
                        detail=normalized.headline,
                        evidence_refs=["logs/execution.log"],
                    )
                )
        blockers = [
            reason.detail
            for reason in status.reasons
            if status.status.value in {"BLOCKED", "PARTIAL"}
        ]
        return RunReport(
            run_id=execution.run_id,
            repository=f"{manifest.owner}/{manifest.repository_name}",
            commit_sha=manifest.commit_sha,
            goal=request.goal,
            documented_setup=[
                step.command for step in plan.steps if step.command is not None
            ],
            plan_steps=[
                ReportPlanStep(
                    step_id=step.step_id,
                    action_type=step.action_type,
                    command=step.command,
                    provenance=step.provenance,
                    source_path=step.source_path,
                    source_location=step.source_location,
                    attempt_number=step.attempt_number,
                )
                for step in plan.steps
            ],
            initial_attempt=ReportAttempt(
                attempt_id="attempt-001",
                source=ReportAttemptSource.OFFICIAL_DOCUMENTED,
                description="Bounded documented-first reproduction plan.",
                commands=[
                    step.command for step in plan.steps if step.command is not None
                ],
                workflow_succeeded=execution.workflow_succeeded,
            ),
            failures=failures,
            diagnoses=[diagnosis] if diagnosis is not None else [],
            verification=verification,
            goal_coverage=verification.goal_coverage if verification else None,
            final_status=status,
            blockers=blockers,
            documentation_gaps=[],
        )

    @staticmethod
    def _write_plan(run_directory: Path, plan: ReproductionPlan) -> None:
        payload = plan.model_dump(mode="json")
        reject_sensitive_mapping(payload)
        serialized = redact_sensitive_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
        )
        if serialized != json.dumps(
            payload, ensure_ascii=False, sort_keys=True, indent=2
        ):
            raise ServiceError("Plan contained credential-like material.")
        (run_directory / "plan.json").write_text(serialized + "\n", encoding="utf-8")


def doctor_report(*, runs_dir: Path | None = None) -> dict[str, object]:
    """Return secret-free local capability checks for the ``doctor`` command."""

    settings = get_llm_settings()
    provider = settings.provider
    configured = (
        provider == "mock"
        or (provider == "gemini" and settings.gemini_api_key is not None)
        or (provider == "groq" and settings.groq_api_key is not None)
    )
    docker: dict[str, object]
    try:
        available = check_docker_available()
        docker = {"available": True, "server_version": available.server_version}
    except SandboxError as exc:
        docker = {"available": False, "error": redact_sensitive_text(str(exc))[:500]}
    resolved_runs_dir = (runs_dir or get_settings().runs_dir).expanduser().resolve()
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "git_available": shutil.which("git") is not None,
        "docker": docker,
        "runs_dir": str(resolved_runs_dir),
        "runs_dir_writable": os.access(
            resolved_runs_dir
            if resolved_runs_dir.exists()
            else resolved_runs_dir.parent,
            os.W_OK,
        ),
        "llm_provider": provider,
        "llm_configured": configured,
    }
