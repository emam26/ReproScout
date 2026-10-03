from __future__ import annotations

import pytest

from reproscout.diagnostics import EvidenceBuilder, FailureClass, normalize_failure
from reproscout.planning import (
    PlanActionType,
    PlanBaseline,
    PlanStep,
    ReproductionPlan,
)
from reproscout.repair import (
    AppliedPlanRepair,
    PlanRepairApplier,
    RepairAction,
    RepairActionType,
    RepairExperimentEngine,
    RepairExperimentStatus,
    RepairLimits,
    RepairNotExecutableError,
    RepairObservation,
    RepairRisk,
    Reversibility,
    validate_repair_action,
)
from reproscout.repair.policy import RepairPolicyError
from reproscout.state import EventType, SQLiteRunStore, Stage


def _context(*, asset_url: str | None = None):
    failure = normalize_failure(
        "",
        "ModuleNotFoundError: No module named 'numpy'",
        exit_code=1,
        timed_out=False,
    )
    dependency_evidence = ["The documented dependency is numpy."]
    if asset_url:
        dependency_evidence.append(f"The README documents asset URL {asset_url}.")
    return EvidenceBuilder().build(
        failure,
        command="python app.py",
        dependency_evidence=dependency_evidence,
    )


def _action(
    action_type: RepairActionType,
    arguments: dict[str, object] | None = None,
    *,
    reversibility: Reversibility = Reversibility.REVERTIBLE,
    risk: RepairRisk = RepairRisk.LOW,
) -> RepairAction:
    return RepairAction(
        action_id="repair-001",
        action_type=action_type,
        reason="The failure evidence supports a bounded experiment.",
        supporting_evidence=["failure:001"],
        expected_effect="The documented failure should be removed or changed.",
        risk=risk,
        reversibility=reversibility,
        arguments=arguments or {},
    )


def _plan() -> ReproductionPlan:
    evidence = []
    return ReproductionPlan(
        repository="https://github.com/example/project",
        commit_sha="abc123",
        goal="Run the documented example.",
        baseline=PlanBaseline.OFFICIAL_DOCUMENTATION,
        steps=[
            PlanStep(
                step_id="step-001",
                action_type=PlanActionType.PREPARE_ENVIRONMENT,
                purpose="Prepare the environment.",
                evidence=evidence,
                timeout_seconds=30,
                expected_outcome="Environment is ready.",
            ),
            PlanStep(
                step_id="step-002",
                action_type=PlanActionType.INSTALL_DEPENDENCY,
                command="python -m pip install -r requirements.txt",
                purpose="Install dependencies.",
                evidence=evidence,
                timeout_seconds=60,
                expected_outcome="Dependencies install.",
            ),
            PlanStep(
                step_id="step-003",
                action_type=PlanActionType.RUN_DEMO,
                command="python app.py",
                purpose="Run the example.",
                evidence=evidence,
                timeout_seconds=60,
                expected_outcome="The example exits successfully.",
            ),
        ],
        overall_timeout_seconds=300,
    )


def _memory_store() -> tuple[SQLiteRunStore, str]:
    store = SQLiteRunStore(":memory:")
    run = store.create_run()
    for stage in (Stage.ANALYZE, Stage.PLAN, Stage.SETUP, Stage.EXECUTE, Stage.DEBUG):
        store.transition(run.run_id, stage)
    return store, run.run_id


@pytest.mark.parametrize(
    ("action_type", "arguments"),
    [
        (
            RepairActionType.CHANGE_INVOCATION,
            {"step_id": "step-003", "command": "python -m pytest"},
        ),
        (RepairActionType.CHANGE_PYTHON_VERSION, {"python_version": "3.11"}),
        (RepairActionType.ADD_DEPENDENCY, {"requirement": "numpy>=1.26"}),
        (RepairActionType.CHANGE_DEPENDENCY_VERSION, {"requirement": "numpy==2.0"}),
        (
            RepairActionType.SET_SAFE_ENVIRONMENT_VARIABLE,
            {"name": "PYTHONWARNINGS", "value": "default"},
        ),
        (RepairActionType.CREATE_REQUIRED_DIRECTORY, {"path": "outputs/cache"}),
        (RepairActionType.ADJUST_CONFIG_PATH, {"path": "configs/default.yaml"}),
        (
            RepairActionType.FETCH_DOCUMENTED_ASSET,
            {"url": "https://example.org/model.bin", "max_bytes": 1024},
        ),
        (
            RepairActionType.APPLY_MINIMAL_PATCH,
            {
                "patch": "diff --git a/app.py b/app.py\n@@\n-print('old')\n+print('new')\n"
            },
        ),
        (RepairActionType.GATHER_MORE_EVIDENCE, {}),
        (RepairActionType.STOP_UNREPAIRABLE, {}),
    ],
)
def test_each_repair_action_family_is_policy_checked(
    action_type: RepairActionType,
    arguments: dict[str, object],
) -> None:
    asset_url = (
        "https://example.org/model.bin"
        if action_type is RepairActionType.FETCH_DOCUMENTED_ASSET
        else None
    )
    context = _context(asset_url=asset_url)
    action = _action(
        action_type,
        arguments,
        reversibility=(
            Reversibility.ROLLBACK_REQUIRED
            if action_type is RepairActionType.APPLY_MINIMAL_PATCH
            else Reversibility.REVERTIBLE
        ),
    )

    validate_repair_action(action, context, limits=RepairLimits())


def test_unsafe_commands_direct_urls_secrets_and_escaping_paths_are_rejected() -> None:
    context = _context()
    cases = [
        _action(
            RepairActionType.CHANGE_INVOCATION,
            {"step_id": "step-003", "command": "python app.py; rm -rf /"},
        ),
        _action(
            RepairActionType.ADD_DEPENDENCY,
            {"requirement": "model @ https://example.org/model.whl"},
        ),
        _action(
            RepairActionType.CREATE_REQUIRED_DIRECTORY,
            {"path": "../outside"},
        ),
        _action(
            RepairActionType.FETCH_DOCUMENTED_ASSET,
            {"url": "https://127.0.0.1/model.bin", "max_bytes": 1024},
        ),
    ]
    for action in cases:
        with pytest.raises(RepairPolicyError):
            validate_repair_action(action, context, limits=RepairLimits())

    with pytest.raises(ValueError, match="Sensitive field"):
        _action(RepairActionType.SET_SAFE_ENVIRONMENT_VARIABLE, {"api_key": "hidden"})


def test_asset_must_be_https_and_documented() -> None:
    context = _context()
    action = _action(
        RepairActionType.FETCH_DOCUMENTED_ASSET,
        {"url": "https://example.org/undocumented.bin", "max_bytes": 1024},
    )

    with pytest.raises(RepairPolicyError, match="trusted evidence"):
        validate_repair_action(action, context, limits=RepairLimits())


def test_minimal_patch_requires_explicit_rollback_metadata() -> None:
    context = _context()
    action = _action(
        RepairActionType.APPLY_MINIMAL_PATCH,
        {"patch": "diff --git a/app.py b/app.py\n"},
    )

    with pytest.raises(RepairPolicyError, match="rollback"):
        validate_repair_action(action, context, limits=RepairLimits())


def test_plan_backend_changes_invocation_and_dependency_without_execution() -> None:
    plan = _plan()
    applier = PlanRepairApplier()

    invocation = applier.apply(
        plan,
        _action(
            RepairActionType.CHANGE_INVOCATION,
            {"step_id": "step-003", "command": "python -m pytest"},
        ),
    )
    assert plan.steps[2].command == "python app.py"
    assert invocation.after.steps[2].command == "python -m pytest"
    assert invocation.rollback() == plan

    dependency = applier.apply(
        plan,
        _action(RepairActionType.ADD_DEPENDENCY, {"requirement": "numpy>=1.26"}),
    )
    assert dependency.after.steps[1].command.endswith("numpy>=1.26")

    with pytest.raises(RepairNotExecutableError):
        applier.apply(
            plan,
            _action(
                RepairActionType.APPLY_MINIMAL_PATCH,
                {"patch": "diff --git a/a b/a"},
                reversibility=Reversibility.ROLLBACK_REQUIRED,
            ),
        )


def test_engine_records_objective_improvement_and_audit_events() -> None:
    store, run_id = _memory_store()
    context = _context()
    application = PlanRepairApplier().apply(
        _plan(),
        _action(
            RepairActionType.CHANGE_INVOCATION,
            {"step_id": "step-003", "command": "python -m pytest"},
        ),
    )
    engine = RepairExperimentEngine(store, _StaticBackend(application))
    after_signature = "a" * 64

    result = engine.run(
        _action(
            RepairActionType.CHANGE_INVOCATION,
            {"step_id": "step-003", "command": "python -m pytest"},
        ),
        context,
        before_failure_signature=context.failure_signature,
        observe=lambda _: RepairObservation(
            workflow_succeeded=False,
            failure_class=FailureClass.TEST_FAILURE,
            failure_signature=after_signature,
            exit_code=1,
            summary="The failure changed to a test failure.",
        ),
        run_id=run_id,
    )

    assert result.status is RepairExperimentStatus.IMPROVED
    assert result.evidence_improved is True
    assert result.rollback_performed is True
    events = store.list_events(run_id)
    assert any(
        event.event_type is EventType.AGENT_ACTION_RECORDED
        and event.payload["agent_action"]["action_type"] == "repair_observed"
        for event in events
    )
    store.close()


def test_engine_blocks_repeated_failure_and_records_stop() -> None:
    store, run_id = _memory_store()
    context = _context()
    action = _action(
        RepairActionType.CHANGE_INVOCATION,
        {"step_id": "step-003", "command": "python -m pytest"},
    )
    application = PlanRepairApplier().apply(_plan(), action)
    engine = RepairExperimentEngine(store, _StaticBackend(application))
    observation = lambda _: RepairObservation(
        workflow_succeeded=False,
        failure_class=FailureClass.MISSING_PACKAGE,
        failure_signature=context.failure_signature,
        exit_code=1,
        summary="The same failure remains.",
    )

    first = engine.run(
        action,
        context,
        before_failure_signature=context.failure_signature,
        observe=observation,
        run_id=run_id,
    )
    second = engine.run(
        action,
        context,
        before_failure_signature=context.failure_signature,
        observe=observation,
        run_id=run_id,
    )

    assert first.status is RepairExperimentStatus.UNCHANGED
    assert second.status is RepairExperimentStatus.STOPPED
    assert "Repeated" in (second.stop_reason or "")
    store.close()


def test_engine_enforces_max_repairs_limit() -> None:
    store, run_id = _memory_store()
    context = _context()
    action = _action(
        RepairActionType.CHANGE_INVOCATION,
        {"step_id": "step-003", "command": "python -m pytest"},
    )
    application = PlanRepairApplier().apply(_plan(), action)
    engine = RepairExperimentEngine(
        store,
        _StaticBackend(application),
        limits=RepairLimits(max_repairs=1),
    )
    observation = lambda _: RepairObservation(
        workflow_succeeded=False,
        failure_class=FailureClass.TEST_FAILURE,
        failure_signature="b" * 64,
        exit_code=1,
        summary="The failure changed.",
    )

    first = engine.run(
        action,
        context,
        before_failure_signature=None,
        observe=observation,
        run_id=run_id,
    )
    second = engine.run(
        action,
        context,
        before_failure_signature=None,
        observe=observation,
        run_id=run_id,
    )

    assert first.status is RepairExperimentStatus.IMPROVED
    assert second.status is RepairExperimentStatus.STOPPED
    assert "Maximum repairs" in (second.stop_reason or "")
    store.close()


def test_engine_rejects_high_risk_and_observation_failure_still_rolls_back() -> None:
    store, run_id = _memory_store()
    context = _context()
    high_risk = _action(
        RepairActionType.CHANGE_INVOCATION,
        {"step_id": "step-003", "command": "python -m pytest"},
        risk=RepairRisk.HIGH,
    )
    engine = RepairExperimentEngine(store, _StaticBackend(None))
    rejected = engine.run(
        high_risk,
        context,
        before_failure_signature=context.failure_signature,
        observe=lambda _: pytest.fail("rejected action was observed"),
        run_id=run_id,
    )
    assert rejected.status is RepairExperimentStatus.REJECTED

    application = _RecordingApplication(_plan(), _plan())
    engine = RepairExperimentEngine(store, _StaticBackend(application))
    action = _action(
        RepairActionType.CHANGE_INVOCATION,
        {"step_id": "step-003", "command": "python -m pytest"},
    )
    with pytest.raises(RuntimeError, match="observer"):
        engine.run(
            action,
            context,
            before_failure_signature=context.failure_signature,
            observe=_raise_observer,
            run_id=run_id,
        )
    assert application.rollback_called is True
    store.close()


class _StaticBackend:
    def __init__(self, application: AppliedPlanRepair | None) -> None:
        self.application = application

    def apply(self, action: RepairAction) -> AppliedPlanRepair:
        if self.application is None:
            raise AssertionError("backend should not be called")
        return self.application


class _RecordingApplication:
    def __init__(self, before: ReproductionPlan, after: ReproductionPlan) -> None:
        self.before = before
        self.after = after
        self.rollback_called = False

    def rollback(self) -> ReproductionPlan:
        self.rollback_called = True
        return self.before


def _raise_observer(_: object) -> RepairObservation:
    raise RuntimeError("observer failure")
