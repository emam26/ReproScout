"""Thin, local-only FastAPI layer over the ReproScout application service."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from reproscout import __version__
from reproscout.application import (
    AuditRequest,
    AuditResult,
    AuditService,
    ReproductionResult,
    ServiceError,
    doctor_report,
)
from reproscout.config import get_settings
from reproscout.security import redact_sensitive_text
from reproscout.state import Event, RunNotFoundError, RunState, SQLiteRunStore

_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_MAX_REPORT_CHARACTERS = 100_000


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    version: str


class DockerStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    server_version: str | None = None
    error: str | None = None


class ConfigResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    python: str
    platform: str
    git_available: bool
    docker: DockerStatus
    runs_dir: str
    runs_dir_writable: bool
    llm_provider: str
    llm_configured: bool


class RunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    stage: str
    outcome: str | None
    context: dict[str, Any]
    created_at: str
    updated_at: str
    report_available: bool


class RunDetail(RunSummary):
    event_count: int = Field(ge=0)
    report: dict[str, Any] | None = None


class EventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    sequence: int = Field(ge=1)
    event_type: str
    stage: str
    timestamp: str
    payload: Any


class ReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    content: str


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail: str


def create_app(
    runs_dir: Path | None = None,
    *,
    service: AuditService | None = None,
) -> FastAPI:
    """Create a local API application with an isolated runs directory.

    This is a local development/control API, not a hardened multi-tenant public
    execution service. Target execution remains behind the existing Docker
    sandbox, and no route accepts arbitrary shell commands or host paths.
    """

    configured_runs = (
        Path(runs_dir if runs_dir is not None else get_settings().runs_dir)
        .expanduser()
        .resolve()
    )
    audit_service = service or AuditService(configured_runs)
    app = FastAPI(
        title="ReproScout local control API",
        version=__version__,
        description=(
            "Local development/control API. Not a hardened multi-tenant public "
            "execution service."
        ),
    )
    router = APIRouter(prefix="/api/v1")

    @router.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__)

    @router.get("/version", response_model=HealthResponse)
    def version() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__)

    @router.get("/config", response_model=ConfigResponse)
    def config() -> ConfigResponse:
        return ConfigResponse.model_validate(
            doctor_report(runs_dir=audit_service.runs_dir)
        )

    @router.post(
        "/audits",
        response_model=AuditResult,
        responses={400: {"model": ErrorResponse}},
        status_code=status.HTTP_201_CREATED,
    )
    def create_audit(request: AuditRequest) -> AuditResult:
        try:
            return audit_service.audit(request)
        except ServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/runs", response_model=list[RunSummary])
    def list_runs() -> list[RunSummary]:
        try:
            with SQLiteRunStore(audit_service.database_path) as store:
                states = store.list_runs()
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail="Run state is unavailable."
            ) from exc
        return [_summary(audit_service.runs_dir, state) for state in states]

    @router.get(
        "/runs/{run_id}",
        response_model=RunDetail,
        responses={404: {"model": ErrorResponse}},
    )
    def get_run(run_id: str) -> RunDetail:
        _validate_run_id(run_id)
        with _store_or_404(audit_service) as store:
            try:
                state = store.get_run(run_id)
                events = store.list_events(run_id)
            except RunNotFoundError as exc:
                raise HTTPException(status_code=404, detail="Run not found.") from exc
        summary = _summary(audit_service.runs_dir, state)
        return RunDetail(
            **summary.model_dump(),
            event_count=len(events),
            report=_read_run_json(audit_service.runs_dir, run_id),
        )

    @router.get(
        "/runs/{run_id}/events",
        response_model=list[EventResponse],
        responses={404: {"model": ErrorResponse}},
    )
    def get_events(run_id: str) -> list[EventResponse]:
        _validate_run_id(run_id)
        with _store_or_404(audit_service) as store:
            try:
                events = store.list_events(run_id)
            except RunNotFoundError as exc:
                raise HTTPException(status_code=404, detail="Run not found.") from exc
        return [_event_response(event) for event in events]

    @router.get(
        "/runs/{run_id}/report",
        response_model=ReportResponse,
        responses={404: {"model": ErrorResponse}},
    )
    def get_report(run_id: str) -> ReportResponse:
        _validate_run_id(run_id)
        path = _safe_run_path(audit_service.runs_dir, run_id, "report.md")
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Report not found.")
        try:
            content = path.read_text(encoding="utf-8")[:_MAX_REPORT_CHARACTERS]
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail="Report is unavailable."
            ) from exc
        return ReportResponse(
            run_id=run_id,
            content=redact_sensitive_text(content),
        )

    @router.post(
        "/runs/{run_id}/reproduce",
        response_model=ReproductionResult,
        responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    )
    def reproduce(run_id: str) -> ReproductionResult:
        _validate_run_id(run_id)
        try:
            return audit_service.reproduce(run_id)
        except ServiceError as exc:
            code = 404 if "not found" in str(exc).lower() else 400
            raise HTTPException(status_code=code, detail=str(exc)) from exc

    app.include_router(router)

    # Small unversioned aliases keep local health checks convenient while all
    # product routes remain under the explicit /api/v1 namespace.
    @app.get("/health", response_model=HealthResponse, include_in_schema=False)
    def root_health() -> HealthResponse:
        return health()

    @app.get("/version", response_model=HealthResponse, include_in_schema=False)
    def root_version() -> HealthResponse:
        return version()

    return app


def run_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    runs_dir: Path | None = None,
) -> None:
    """Run the local API with Uvicorn using a loopback default."""

    import uvicorn

    uvicorn.run(create_app(runs_dir), host=host, port=port, log_level="info")


def _validate_run_id(run_id: str) -> None:
    if not _SAFE_RUN_ID.fullmatch(run_id):
        raise HTTPException(
            status_code=400, detail="Run ID contains unsupported path characters."
        )


def _safe_run_path(root: Path, run_id: str, filename: str) -> Path:
    base = root.expanduser().resolve()
    run_directory_input = base / run_id
    if run_directory_input.is_symlink():
        raise HTTPException(
            status_code=400, detail="Run directory cannot be a symlink."
        )
    run_directory = run_directory_input.resolve()
    try:
        run_directory.relative_to(base)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="Run path escapes the configured directory."
        ) from exc
    target = (run_directory / filename).resolve()
    try:
        target.relative_to(run_directory)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="Artifact path escapes the run directory."
        ) from exc
    return target


def _store_or_404(service: AuditService) -> SQLiteRunStore:
    try:
        return SQLiteRunStore(service.database_path)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail="Run state is unavailable."
        ) from exc


def _summary(root: Path, state: RunState) -> RunSummary:
    report = (root / state.run_id / "report.md").is_file()
    return RunSummary(
        run_id=state.run_id,
        stage=state.stage.value,
        outcome=state.outcome.value if state.outcome else None,
        context=_safe_value(dict(state.context)),
        created_at=state.created_at.isoformat(),
        updated_at=state.updated_at.isoformat(),
        report_available=report,
    )


def _event_response(event: Event) -> EventResponse:
    return EventResponse(
        run_id=event.run_id,
        sequence=event.sequence,
        event_type=event.event_type.value,
        stage=event.stage.value,
        timestamp=event.timestamp.isoformat(),
        payload=_safe_value(event.payload),
    )


def _read_run_json(root: Path, run_id: str) -> dict[str, Any] | None:
    path = _safe_run_path(root, run_id, "run.json")
    if not path.is_file():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _safe_value(parsed)


def _safe_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_sensitive_text(value)[:20_000]
    if isinstance(value, Mapping):
        return {
            str(key): _safe_value(item)
            for key, item in value.items()
            if not _looks_sensitive_key(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value[:1_000]]
    return value


def _looks_sensitive_key(key: str) -> bool:
    normalized = key.replace("-", "_").lower()
    return any(
        marker in normalized
        for marker in (
            "api_key",
            "token",
            "password",
            "secret",
            "credential",
            "private_key",
        )
    )
