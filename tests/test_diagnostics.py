from __future__ import annotations

import json
from pathlib import Path

import pytest

from reproscout.diagnostics import (
    AssetKind,
    AssetScanLimits,
    EvidenceBuilder,
    EvidenceLimits,
    FailureClass,
    PipDiagnosticRunner,
    classify_failure,
    collect_environment_fingerprint,
    compare_imports_to_requirements,
    contract_from_plan,
    detect_python_imports,
    detect_repository_assets,
    failure_signature,
    normalize_failure,
    parse_pip_check,
    parse_pip_inspect,
    parse_pip_report,
    parse_requirements_text,
    requirement_marker_applies,
    version_satisfies,
)
from reproscout.planning import (
    PlanActionType,
    PlanBaseline,
    PlanStep,
    ReproductionPlan,
    RiskLevel,
)
from reproscout.sandbox import ExecutionResult, Sandbox, SandboxConfig


@pytest.mark.parametrize(
    ("message", "exit_code", "timed_out", "expected"),
    [
        (
            "Package requires-python >=3.12",
            1,
            False,
            FailureClass.PYTHON_VERSION_MISMATCH,
        ),
        ("ERROR: ResolutionImpossible", 1, False, FailureClass.DEPENDENCY_CONFLICT),
        (
            "ModuleNotFoundError: No module named 'torch'",
            1,
            False,
            FailureClass.MISSING_PACKAGE,
        ),
        (
            "ImportError: cannot import name x from y",
            1,
            False,
            FailureClass.IMPORT_ERROR,
        ),
        (
            "error: command 'cc' failed with exit code 1",
            1,
            False,
            FailureClass.BUILD_FAILURE,
        ),
        ("cmake: command not found", 1, False, FailureClass.BUILD_TOOL_MISSING),
        (
            "libfoo.so: cannot open shared object file",
            1,
            False,
            FailureClass.SYSTEM_LIBRARY_MISSING,
        ),
        ("CUDA driver version is insufficient", 1, False, FailureClass.CUDA_MISMATCH),
        ("RuntimeError: CUDA is not available", 1, False, FailureClass.GPU_REQUIRED),
        ("fatal: out of memory", 137, False, FailureClass.OUT_OF_MEMORY),
        ("command exceeded deadline", 124, True, FailureClass.TIMEOUT),
        ("FileNotFoundError: config.json", 1, False, FailureClass.FILE_NOT_FOUND),
        ("checkpoint weights.pth not found", 1, False, FailureClass.CHECKPOINT_MISSING),
        ("dataset directory is missing", 1, False, FailureClass.DATASET_MISSING),
        ("HTTP Error 404: Not Found", 1, False, FailureClass.DEAD_URL),
        ("HTTP Error 401: Unauthorized", 1, False, FailureClass.AUTH_REQUIRED),
        (
            "Temporary failure in name resolution",
            1,
            False,
            FailureClass.NETWORK_FAILURE,
        ),
        ("PermissionError: permission denied", 1, False, FailureClass.PERMISSION_ERROR),
        ("YAML ParserError in config", 1, False, FailureClass.CONFIG_ERROR),
        (
            "=== short test summary info ===\n1 failed in 0.1s",
            1,
            False,
            FailureClass.TEST_FAILURE,
        ),
        ("unexpected opaque failure", 3, False, FailureClass.UNKNOWN),
    ],
)
def test_failure_taxonomy(
    message: str,
    exit_code: int,
    timed_out: bool,
    expected: FailureClass,
) -> None:
    assert (
        classify_failure("", message, exit_code=exit_code, timed_out=timed_out)
        is expected
    )


def test_traceback_normalization_is_bounded_and_references_full_log() -> None:
    traceback = "\n".join(
        [
            "Traceback (most recent call last):",
            '  File "/tmp/run/app.py", line 42, in <module>',
            "    import missing_package",
            "ModuleNotFoundError: No module named 'missing_package'",
            "GITHUB_TOKEN=should-never-survive",
            *(f"noise {index}" for index in range(10_000)),
        ]
    )

    failure = normalize_failure(
        "",
        traceback,
        exit_code=1,
        timed_out=False,
        log_reference="logs/execution.log",
    )

    assert failure.failure_class is FailureClass.MISSING_PACKAGE
    assert failure.log_reference == "logs/execution.log"
    assert failure.excerpt_character_count <= 12_000
    assert failure.truncated is True
    assert "should-never-survive" not in "\n".join(failure.important_lines)
    assert "<volatile>" in "\n".join(failure.important_lines)


def test_packaging_parses_requirements_specifiers_markers_and_errors() -> None:
    result = parse_requirements_text(
        "requests>=2.31; python_version >= '3.11'\n"
        "opencv-python==4.10.0.84\n"
        "not a valid requirement @@@\n",
        source="requirements.txt",
    )

    assert [item.canonical_name for item in result.requirements] == [
        "requests",
        "opencv-python",
    ]
    assert str(result.requirements[0].marker) == 'python_version >= "3.11"'
    assert result.requirements[1].specifier == "==4.10.0.84"
    assert result.errors == ["line 3: invalid PEP 508 requirement"]
    assert version_satisfies("2.32.1", ">=2.31,<3") is True
    assert (
        requirement_marker_applies(
            result.requirements[0],
            {"python_version": "3.12"},
        )
        is True
    )


def test_pip_machine_outputs_are_parsed() -> None:
    report = parse_pip_report(
        json.dumps(
            {
                "pip_version": "25.1",
                "install": [
                    {
                        "requested": True,
                        "metadata": {
                            "name": "demo",
                            "version": "1.2",
                            "requires_dist": ["requests>=2"],
                        },
                    }
                ],
            }
        )
    )
    inspect = parse_pip_inspect(
        json.dumps(
            {
                "pip_version": "25.1",
                "installed": [
                    {
                        "requested": False,
                        "metadata": {"name": "requests", "version": "2.32"},
                    }
                ],
            }
        )
    )
    issues = parse_pip_check(
        "demo 1.2 has requirement requests<2, but you have requests 2.32.\n"
    )

    assert report.install[0].dependencies == ["requests>=2"]
    assert report.install[0].requested is True
    assert inspect.installed[0].name == "requests"
    assert issues[0].package == "demo"


class ScriptedSandbox(Sandbox):
    def __init__(self, config: SandboxConfig, results: list[ExecutionResult]) -> None:
        self.config = config
        self.results = results
        self.commands: list[str | list[str]] = []

    def create(self) -> None:
        pass

    def execute(self, command, *, timeout_seconds=None) -> ExecutionResult:
        del timeout_seconds
        self.commands.append(command)
        return self.results.pop(0)

    def destroy(self) -> None:
        pass


def _execution(stdout: str, *, exit_code: int = 0) -> ExecutionResult:
    return ExecutionResult(
        command="fixture",
        stdout=stdout,
        stderr="",
        exit_code=exit_code,
        duration=0.01,
        timed_out=False,
        container_id="fixture-container",
    )


def test_pip_diagnostics_use_only_the_sandbox(tmp_path: Path) -> None:
    config = SandboxConfig(workspace_path=tmp_path, run_id="diagnostic-pip")
    report_json = json.dumps(
        {
            "pip_version": "25.1",
            "install": [
                {"requested": True, "metadata": {"name": "demo", "version": "1"}}
            ],
        }
    )
    inspect_json = json.dumps(
        {
            "pip_version": "25.1",
            "installed": [
                {"requested": True, "metadata": {"name": "pip", "version": "25.1"}}
            ],
        }
    )
    sandbox = ScriptedSandbox(
        config,
        [
            _execution(report_json),
            _execution(inspect_json),
            _execution("No broken requirements found.\n"),
        ],
    )
    runner = PipDiagnosticRunner(sandbox)

    assert runner.dry_run_requirements("requirements.txt").install[0].name == "demo"
    assert runner.inspect().installed[0].name == "pip"
    assert runner.check() == []
    assert sandbox.commands[0] == [
        "python",
        "-m",
        "pip",
        "install",
        "--dry-run",
        "--ignore-installed",
        "--report",
        "-",
        "-r",
        "/workspace/requirements.txt",
    ]


def test_environment_fingerprint_excludes_secret_names(tmp_path: Path) -> None:
    config = SandboxConfig(
        workspace_path=tmp_path,
        run_id="fingerprint",
        network="none",
    )
    payload = json.dumps(
        {
            "os_name": "Linux",
            "os_release": "6.1",
            "architecture": "x86_64",
            "python_version": "3.11.9",
            "pip_version": "25.1",
            "installed_packages": [{"name": "pip", "version": "25.1"}],
            "gpu_visible": False,
            "cuda_runtime": None,
            "compiler": "GCC 12",
            "environment_variable_names": [
                "PATH",
                "HOME",
                "GEMINI_API_KEY",
                "ACCESS_TOKEN",
            ],
        }
    )
    sandbox = ScriptedSandbox(config, [_execution(payload)])

    fingerprint = collect_environment_fingerprint(
        sandbox,
        repository_commit_sha="a" * 40,
        config=config,
        container_image_digest="sha256:fixture",
    )

    assert fingerprint.environment_variable_names == ["HOME", "PATH"]
    assert fingerprint.container_image_digest == "sha256:fixture"
    assert fingerprint.network_mode == "none"
    assert sandbox.commands[0][0:2] == ["python", "-c"]


def test_ast_import_detection_and_distribution_mapping(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "import cv2\nfrom PIL import Image\nimport sklearn\nimport requests\nimport os\n",
        encoding="utf-8",
    )
    requirements = parse_requirements_text(
        "opencv-python\nPillow\nrequests>=2\n",
        source="requirements.txt",
    ).requirements

    imports = detect_python_imports(tmp_path)
    findings = compare_imports_to_requirements(imports, requirements)
    by_module = {finding.module: finding for finding in findings}

    assert by_module["cv2"].declared is True
    assert by_module["cv2"].heuristic_mapping is True
    assert by_module["PIL"].candidate_distributions == ["Pillow"]
    assert by_module["requests"].declared is True
    assert by_module["sklearn"].declared is False
    assert "os" not in by_module


def test_failure_signature_is_stable_for_volatile_details() -> None:
    first = normalize_failure(
        "",
        'File "/tmp/run-a/app.py", line 12\nModuleNotFoundError: No module named x',
        exit_code=1,
        timed_out=False,
    )
    second = normalize_failure(
        "",
        'File "/tmp/run-b/app.py", line 99\nModuleNotFoundError: No module named x',
        exit_code=1,
        timed_out=False,
    )

    assert failure_signature(first, command="python app.py") == failure_signature(
        second,
        command="python   app.py",
    )


def test_evidence_builder_enforces_bounds_and_redacts() -> None:
    failure = normalize_failure(
        "",
        "ModuleNotFoundError: No module named demo",
        exit_code=1,
        timed_out=False,
        log_reference="logs/execution.log",
    )
    context = EvidenceBuilder(
        EvidenceLimits(max_characters=600, max_item_characters=250, max_items=6)
    ).build(
        failure,
        command="python app.py",
        dependency_evidence=["GITHUB_TOKEN=top-secret " + "x" * 500],
        documentation=[("README.md", "# Install\npip install demo\n" + "z" * 500)],
        previous_attempts=["same command failed"],
    )
    serialized = context.model_dump_json()

    assert context.total_characters <= 600
    assert len(context.evidence) <= 6
    assert context.truncated is True
    assert "top-secret" not in serialized
    assert "<redacted>" in serialized
    assert context.evidence[0].reference == "failure:001"


def test_asset_lfs_submodule_and_dvc_indicators_are_detected(tmp_path: Path) -> None:
    (tmp_path / "weights.pth").write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 123\n",
        encoding="utf-8",
    )
    (tmp_path / ".gitmodules").write_text(
        '[submodule "vendor"]\npath = vendor\nurl = https://example.test/vendor.git\n',
        encoding="utf-8",
    )
    (tmp_path / "data.dvc").write_text("outs:\n- path: data\n", encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "Download https://user:secret@example.test/model.ckpt?token=bad\n",
        encoding="utf-8",
    )

    assets = detect_repository_assets(
        tmp_path,
        AssetScanLimits(max_files=20, max_file_bytes=10_000, max_references=20),
    )
    kinds = {reference.kind for reference in assets.references}
    values = "\n".join(reference.value for reference in assets.references)

    assert AssetKind.CHECKPOINT in kinds
    assert AssetKind.GIT_LFS_POINTER in kinds
    assert AssetKind.GIT_SUBMODULE in kinds
    assert AssetKind.DVC in kinds
    assert AssetKind.EXTERNAL_URL in kinds
    assert "secret" not in values
    assert "token=bad" not in values


def test_verification_contract_is_derived_without_claiming_success() -> None:
    plan = ReproductionPlan(
        repository="example/project",
        commit_sha="a" * 40,
        goal="tests",
        baseline=PlanBaseline.OFFICIAL_DOCUMENTATION,
        steps=[
            PlanStep(
                step_id="step-001",
                action_type=PlanActionType.INSTALL_DEPENDENCY,
                command="python -m pip install .",
                purpose="Install project.",
                timeout_seconds=60,
                expected_outcome="Installation exits successfully.",
                risk=RiskLevel.MEDIUM,
            ),
            PlanStep(
                step_id="step-002",
                action_type=PlanActionType.RUN_TESTS,
                command="python -m pytest",
                purpose="Run tests.",
                timeout_seconds=60,
                expected_outcome="Tests execute.",
                risk=RiskLevel.LOW,
            ),
        ],
        overall_timeout_seconds=120,
    )

    contract = contract_from_plan(plan)

    assert contract.goal == "tests"
    assert [target.target_id for target in contract.targets] == [
        "verify-001",
        "verify-002",
    ]
    assert contract.targets[1].command == "python -m pytest"
    assert "REPRODUCED" not in contract.model_dump_json()
