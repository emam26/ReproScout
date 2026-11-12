from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from reproscout.api import create_app, run_server
from reproscout.application import (
    AuditRequest,
    AuditResult,
    PreflightRequest,
    PreflightResult,
    ReproductionResult,
    ServiceError,
)
from reproscout.diagnostics.models import VerificationTargetType
from reproscout.preflight import PreflightArtifactPaths, PreflightStatus
from reproscout.state import EventType, SQLiteRunStore, Stage
from reproscout.status import compute_reproduction_status
from reproscout.verification import (
    VerificationCheck,
    VerificationCheckStatus,
    VerificationEvidence,
    VerificationLevel,
    VerificationResult,
    VerificationResultStatus,
)


class StubAuditService:
    def __init__(self, root: Path) -> None:
        self.runs_dir = root
        self.runs_dir.mkdir(parents=True)
        self.database_path = self.runs_dir / "state.sqlite3"
        self.audit_calls: list[AuditRequest] = []
        self.preflight_calls: list[PreflightRequest] = []
        self.reproduce_calls: list[str] = []
        self.fail_audit = False
        self._create_fixture_run()

    def _create_fixture_run(self) -> None:
        with SQLiteRunStore(self.database_path) as store:
            run = store.create_run(
                run_id="api-run",
                context={
                    "repository": "example/project",
                    "message": "Bearer test-token",
                },
            )
            store.transition(run.run_id, Stage.ANALYZE)
            store.append_event(
                run.run_id,
                EventType.AGENT_ACTION_RECORDED,
                {"message": "GITHUB_TOKEN=fixture-secret"},
            )
        run_dir = self.runs_dir / "api-run"
        run_dir.mkdir()
        (run_dir / "report.md").write_text(
            "# Report\n\nAuthorization: Bearer fixture-secret\n", encoding="utf-8"
        )
        (run_dir / "run.json").write_text(
            json.dumps({"message": "API_KEY=fixture-secret"}), encoding="utf-8"
        )
        (run_dir / "environment-report.md").write_text(
            "# Environment\n\nAuthorization: Bearer fixture-secret\n",
            encoding="utf-8",
        )
        (run_dir / "agent-handoff.md").write_text(
            "# Handoff\n\nTOKEN=fixture-secret\n", encoding="utf-8"
        )
        (run_dir / "environment-evidence.json").write_text(
            json.dumps({"status": "UNVERIFIED", "token": "fixture-secret"}),
            encoding="utf-8",
        )

    def audit(self, request: AuditRequest) -> AuditResult:
        self.audit_calls.append(request)
        if self.fail_audit:
            raise ServiceError("controlled audit failure")
        return AuditResult(
            run_id="api-run",
            repository="example/project",
            commit_sha="a" * 40,
            goal=request.goal,
            stage=Stage.ANALYZE,
            status=compute_reproduction_status(None, workflow_succeeded=False),
            attempts=0,
            repairs=0,
            report_path=self.runs_dir / "api-run" / "report.md",
        )

    def preflight(self, request: PreflightRequest) -> PreflightResult:
        self.preflight_calls.append(request)
        return PreflightResult(
            run_id="api-run",
            repository="example/project",
            commit_sha="a" * 40,
            status=PreflightStatus.UNVERIFIED,
            verified_environment=False,
            artifact_paths=PreflightArtifactPaths(
                run_directory=self.runs_dir / "api-run",
                environment_report=self.runs_dir / "api-run" / "environment-report.md",
                environment_evidence=self.runs_dir
                / "api-run"
                / "environment-evidence.json",
                agent_handoff=self.runs_dir / "api-run" / "agent-handoff.md",
            ),
        )

    def reproduce(self, run_id: str) -> ReproductionResult:
        self.reproduce_calls.append(run_id)
        return ReproductionResult(
            source_run_id=run_id,
            clean_run_id="clean-api-run",
            status=compute_reproduction_status(None, workflow_succeeded=False),
            verification=VerificationResult(
                status=VerificationResultStatus.UNSPECIFIED,
                level=VerificationLevel.L0,
                contract_goal="fixture",
                checks=[
                    VerificationCheck(
                        check_id="check-001",
                        target_id="verify-001",
                        target_type=VerificationTargetType.COMMAND_EXITS_SUCCESSFULLY,
                        level=VerificationLevel.L0,
                        status=VerificationCheckStatus.UNAVAILABLE,
                        reason="fixture",
                        evidence=[
                            VerificationEvidence(
                                evidence_id="evidence-001",
                                source="fixture",
                                detail="fixture",
                            )
                        ],
                    )
                ],
                summary="fixture",
            ),
            package_path=self.runs_dir / "api-run",
        )


def test_api_health_version_and_openapi(tmp_path: Path, monkeypatch) -> None:
    service = StubAuditService(tmp_path / "runs")

    def doctor_must_not_run(*, runs_dir: Path) -> dict[str, object]:
        del runs_dir
        raise AssertionError("health must not inspect Docker")

    monkeypatch.setattr(
        "reproscout.api.app.doctor_report",
        doctor_must_not_run,
    )
    client = TestClient(create_app(service.runs_dir, service=service))

    assert client.get("/api/v1/health").json()["status"] == "ok"
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/version").json()["version"] == "0.1.0"
    assert client.get("/openapi.json").status_code == 200


def test_server_rejects_non_loopback_bind_address() -> None:
    try:
        run_server(host="0.0.0.0")
    except ValueError as exc:
        assert "loopback" in str(exc)
    else:
        raise AssertionError("Expected a non-loopback API bind to be rejected.")


def test_api_config_reports_secret_free_runtime_status(
    tmp_path: Path, monkeypatch
) -> None:
    service = StubAuditService(tmp_path / "runs")
    monkeypatch.setattr(
        "reproscout.api.app.doctor_report",
        lambda *, runs_dir: {
            "python": "3.12.0",
            "platform": "test-platform",
            "git_available": True,
            "docker": {
                "available": False,
                "daemon_running": True,
                "context_verified": False,
                "server_version": "27.0",
                "error": "daemon unavailable",
                "command": ["docker", "--context", "secret-context"],
                "sandbox_security": {"seccomp": "unverified"},
            },
            "runs_dir": str(runs_dir),
            "runs_dir_writable": True,
            "llm_provider": "mock",
            "llm_configured": True,
        },
    )
    client = TestClient(create_app(service.runs_dir, service=service))

    response = client.get("/api/v1/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["docker"]["available"] is False
    assert payload["docker"]["daemon_running"] is True
    assert payload["docker"]["context_verified"] is False
    assert payload["docker"]["seccomp"] == "unverified"
    assert "command" not in payload["docker"]
    assert payload["llm_configured"] is True
    assert payload["runs_dir"] == str(service.runs_dir.resolve())
    assert "api_key" not in response.text.lower()


def test_api_audit_validation_and_core_translation(tmp_path: Path) -> None:
    service = StubAuditService(tmp_path / "runs")
    client = TestClient(create_app(service.runs_dir, service=service))

    created = client.post(
        "/api/v1/audits",
        json={"repository_url": "https://github.com/example/project", "no_ai": True},
    )
    assert created.status_code == 201
    assert created.json()["run_id"] == "api-run"
    assert service.audit_calls[0].no_ai is True

    invalid = client.post(
        "/api/v1/audits",
        json={"repository_url": "https://github.com/example/project", "goal": "train"},
    )
    assert invalid.status_code == 422

    service.fail_audit = True
    translated = client.post(
        "/api/v1/audits",
        json={"repository_url": "https://github.com/example/project"},
    )
    assert translated.status_code == 400
    assert translated.json() == {"detail": "controlled audit failure"}


def test_api_preflight_and_bounded_artifacts(tmp_path: Path) -> None:
    service = StubAuditService(tmp_path / "runs")
    client = TestClient(create_app(service.runs_dir, service=service))

    created = client.post(
        "/api/v1/preflights",
        json={"repository_url": "https://github.com/example/project"},
    )
    assert created.status_code == 201
    assert created.json()["status"] == "UNVERIFIED"
    assert service.preflight_calls[0].no_ai is True

    report = client.get("/api/v1/runs/api-run/environment-report")
    handoff = client.get("/api/v1/runs/api-run/agent-handoff")
    evidence = client.get("/api/v1/runs/api-run/environment-evidence")
    assert report.status_code == 200
    assert handoff.status_code == 200
    assert evidence.status_code == 200
    assert "fixture-secret" not in report.text
    assert "fixture-secret" not in handoff.text
    assert "fixture-secret" not in evidence.text
    assert evidence.json()["evidence"]["status"] == "UNVERIFIED"


def test_api_runs_detail_events_report_and_secret_redaction(tmp_path: Path) -> None:
    service = StubAuditService(tmp_path / "runs")
    client = TestClient(create_app(service.runs_dir, service=service))

    listed = client.get("/api/v1/runs")
    assert listed.status_code == 200
    assert listed.json()[0]["run_id"] == "api-run"

    detail = client.get("/api/v1/runs/api-run")
    assert detail.status_code == 200
    detail_text = detail.text
    assert "fixture-secret" not in detail_text
    assert "Bearer <redacted>" in detail_text

    events = client.get("/api/v1/runs/api-run/events")
    assert events.status_code == 200
    assert "fixture-secret" not in events.text
    assert "<redacted>" in events.text

    report = client.get("/api/v1/runs/api-run/report")
    assert report.status_code == 200
    assert "fixture-secret" not in report.text
    assert "Authorization: <redacted>" in report.text

    assert client.get("/api/v1/runs/missing").status_code == 404
    assert client.get("/api/v1/runs/bad%5Coutside").status_code == 400


def test_api_reproduce_uses_shared_service(tmp_path: Path) -> None:
    service = StubAuditService(tmp_path / "runs")
    client = TestClient(create_app(service.runs_dir, service=service))

    response = client.post("/api/v1/runs/api-run/reproduce")

    assert response.status_code == 200
    assert response.json()["source_run_id"] == "api-run"
    assert service.reproduce_calls == ["api-run"]
