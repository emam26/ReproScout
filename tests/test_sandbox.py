from __future__ import annotations

import base64
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

import reproscout.sandbox.docker as docker_module
from reproscout.sandbox import (
    DockerCommandError,
    DockerSandbox,
    ResourceLimits,
    SandboxConfig,
    SandboxError,
    SandboxSecurityError,
    check_docker_available,
    resolve_docker_command,
)


def test_resource_limits_are_configurable() -> None:
    limits = ResourceLimits(
        memory="256m",
        cpus=0.5,
        pids_limit=32,
        command_timeout_seconds=7,
        create_timeout_seconds=45,
    )

    assert limits.memory == "256m"
    assert limits.cpus == 0.5
    assert limits.pids_limit == 32
    assert limits.command_timeout_seconds == 7
    assert limits.create_timeout_seconds == 45


def test_docker_run_arguments_have_safety_and_ownership_defaults(
    tmp_path: Path,
) -> None:
    sandbox = DockerSandbox(
        SandboxConfig(workspace_path=tmp_path, run_id="unit-test"),
        docker_command=("docker",),
    )

    if sys.platform != "linux":
        with pytest.raises(SandboxSecurityError, match="native Linux and WSL"):
            sandbox._build_run_args()
        return
    owner = tmp_path.stat()
    if os.geteuid() == 0:
        with pytest.raises(SandboxSecurityError, match="non-root"):
            sandbox._build_run_args()
        return

    arguments = sandbox._build_run_args()

    assert "--privileged" not in arguments
    assert "--network=host" not in arguments
    assert "/var/run/docker.sock" not in " ".join(arguments)
    assert "--cap-drop=ALL" in arguments
    assert "--security-opt=no-new-privileges" in arguments
    assert "--read-only" in arguments
    assert "--memory=512m" in arguments
    assert "--cpus=1" in arguments
    assert "--pids-limit=128" in arguments
    assert "reproscout.managed=true" in arguments
    assert "reproscout.run_id=unit-test" in arguments
    assert "--user" in arguments
    assert any(
        argument.startswith("/runtime:rw,nosuid,nodev,size=256m,")
        for argument in arguments
    )
    assert (
        "PATH=/runtime/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/bin:/sbin:/bin"
        in arguments
    )
    assert "python -m venv --copies /runtime/venv" in " ".join(arguments)
    assert "/workspace/.reproscout/venv" not in " ".join(arguments)
    expected_uid = owner.st_uid if owner.st_uid > 0 else os.geteuid()
    expected_gid = (
        owner.st_gid
        if owner.st_uid == os.geteuid() and owner.st_gid > 0
        else os.getegid()
    )
    assert arguments[arguments.index("--user") + 1] == (
        f"{expected_uid}:{expected_gid}"
    )
    runtime_mount = next(
        argument for argument in arguments if argument.startswith("/runtime:")
    )
    assert f"uid={expected_uid}" in runtime_mount
    assert f"gid={expected_gid}" in runtime_mount
    assert any("target=/workspace" in argument for argument in arguments)


def test_docker_container_names_are_unique_but_labels_remain_stable(
    tmp_path: Path,
) -> None:
    config = SandboxConfig(workspace_path=tmp_path, run_id="same-run")
    first = DockerSandbox(config, docker_command=("docker",))
    second = DockerSandbox(config, docker_command=("docker",))

    assert first.container_name != second.container_name
    assert first.labels == second.labels


def test_sandbox_instances_use_private_runtime_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing_venv = tmp_path / ".reproscout" / "venv" / "bin"
    existing_venv.mkdir(parents=True)
    (existing_venv / "python").write_text("repository-controlled\n", encoding="utf-8")
    first = DockerSandbox(
        SandboxConfig(workspace_path=tmp_path, run_id="first"),
        docker_command=("docker",),
    )
    second = DockerSandbox(
        SandboxConfig(workspace_path=tmp_path, run_id="second"),
        docker_command=("docker",),
    )

    monkeypatch.setattr(first, "_workspace_user", lambda: "1001:1002")
    monkeypatch.setattr(second, "_workspace_user", lambda: "1001:1002")
    first_args = first._build_run_args()
    second_args = second._build_run_args()

    assert first.container_name != second.container_name
    first_runtime = next(
        argument for argument in first_args if argument.startswith("/runtime:")
    )
    second_runtime = next(
        argument for argument in second_args if argument.startswith("/runtime:")
    )
    assert first_runtime == second_runtime
    assert "uid=1001,gid=1002,mode=700" in first_runtime
    assert "/runtime/venv/bin" in next(
        argument for argument in first_args if argument.startswith("PATH=")
    )
    assert "/workspace/.reproscout/venv" not in " ".join(first_args)


def test_workspace_without_write_access_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform != "linux":
        pytest.skip("POSIX ownership checks are Linux/WSL-specific")
    sandbox = DockerSandbox(
        SandboxConfig(workspace_path=tmp_path, run_id="permission-test"),
        docker_command=("docker",),
    )
    monkeypatch.setattr(docker_module.os, "access", lambda *_args: False)

    with pytest.raises(SandboxSecurityError, match="cannot write"):
        sandbox._build_run_args()


def test_managed_python_venv_lib64_symlink_is_allowed(tmp_path: Path) -> None:
    venv = tmp_path / ".reproscout" / "venv"
    (venv / "lib").mkdir(parents=True)
    (venv / "bin").mkdir()
    (venv / "bin" / "python").write_text("managed\n", encoding="utf-8")
    (venv / "pyvenv.cfg").write_text("home = /usr/local\n", encoding="utf-8")
    try:
        (venv / "lib64").symlink_to("lib", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable in this environment")

    sandbox = DockerSandbox(
        SandboxConfig(workspace_path=tmp_path, run_id="managed-venv"),
        docker_command=("docker",),
    )

    sandbox._validate_workspace()


def test_unexpected_workspace_directory_symlink_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-directory"
    outside.mkdir()
    try:
        (tmp_path / "linked-directory").symlink_to(
            outside, target_is_directory=True
        )
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable in this environment")

    sandbox = DockerSandbox(
        SandboxConfig(workspace_path=tmp_path, run_id="unexpected-link"),
        docker_command=("docker",),
    )

    with pytest.raises(SandboxSecurityError, match="symlinked directories"):
        sandbox._validate_workspace()


def test_managed_venv_symlink_cannot_escape_workspace(tmp_path: Path) -> None:
    venv = tmp_path / ".reproscout" / "venv"
    (venv / "lib").mkdir(parents=True)
    outside = tmp_path.parent / "outside-library"
    outside.mkdir()
    try:
        (venv / "lib64").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable in this environment")

    sandbox = DockerSandbox(
        SandboxConfig(workspace_path=tmp_path, run_id="escaping-venv"),
        docker_command=("docker",),
    )

    with pytest.raises(SandboxSecurityError, match="symlinked directories"):
        sandbox._validate_workspace()


def test_managed_directory_cannot_impersonate_venv_structure(tmp_path: Path) -> None:
    managed = tmp_path / ".reproscout" / "venv"
    managed.mkdir(parents=True)
    outside = tmp_path.parent / "outside-managed"
    outside.mkdir()
    try:
        (managed / "unexpected").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable in this environment")

    sandbox = DockerSandbox(
        SandboxConfig(workspace_path=tmp_path, run_id="impersonated-venv"),
        docker_command=("docker",),
    )

    with pytest.raises(SandboxSecurityError, match="symlinked directories"):
        sandbox._validate_workspace()


def test_non_linux_workspace_ownership_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = DockerSandbox(
        SandboxConfig(workspace_path=tmp_path, run_id="platform-test"),
        docker_command=("docker",),
    )
    monkeypatch.setattr(docker_module.sys, "platform", "win32")

    with pytest.raises(SandboxSecurityError, match="native Linux and WSL"):
        sandbox._build_run_args()


def test_docker_startup_timeout_preserves_sanitized_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = DockerSandbox(
        SandboxConfig(workspace_path=tmp_path, run_id="startup-timeout"),
        docker_command=("docker",),
    )
    monkeypatch.setattr(sandbox, "_build_run_args", lambda: ["run"])
    monkeypatch.setattr(docker_module, "check_docker_available", lambda _command: None)
    removed = False

    def remove_by_name() -> None:
        nonlocal removed
        removed = True

    def invoke(_args, *, timeout_seconds):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired(
            ["docker", "run"],
            timeout_seconds,
            output=b"startup output\n",
            stderr=b"permission denied TOKEN=secret\n",
        )

    monkeypatch.setattr(sandbox, "_remove_by_name", remove_by_name)
    monkeypatch.setattr(sandbox, "_invoke", invoke)

    with pytest.raises(DockerCommandError) as error:
        sandbox.create()

    message = str(error.value)
    assert "timed out" in message
    assert "startup output" in message
    assert "permission denied" in message
    assert "secret" not in message
    assert removed is True


def test_failed_creation_does_not_remove_unowned_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = DockerSandbox(
        SandboxConfig(workspace_path=tmp_path, run_id="name-collision"),
        docker_command=("docker",),
    )
    monkeypatch.setattr(sandbox, "_build_run_args", lambda: ["run"])
    monkeypatch.setattr(docker_module, "check_docker_available", lambda _command: None)
    calls: list[list[str]] = []

    def invoke(args, *, timeout_seconds):  # type: ignore[no-untyped-def]
        del timeout_seconds
        calls.append(list(args))
        if args[0] == "run":
            return subprocess.CompletedProcess(
                args,
                125,
                stdout="",
                stderr="already exists",
            )
        assert args[0] == "ps"
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="a" * 64 + "\t/other-user-container\n",
            stderr="",
        )

    monkeypatch.setattr(sandbox, "_invoke", invoke)

    with pytest.raises(DockerCommandError, match="already exists"):
        sandbox.create()

    assert [call[0] for call in calls] == ["run", "ps"]


def test_docker_startup_failure_collects_logs_before_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = DockerSandbox(
        SandboxConfig(
            workspace_path=tmp_path,
            run_id="startup-failure",
            resource_limits=ResourceLimits(create_timeout_seconds=1),
        ),
        docker_command=("docker",),
    )
    monkeypatch.setattr(sandbox, "_build_run_args", lambda: ["run"])
    monkeypatch.setattr(docker_module, "check_docker_available", lambda _command: None)
    monkeypatch.setattr(docker_module.time, "sleep", lambda _seconds: None)
    clock = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(docker_module.time, "monotonic", lambda: next(clock))
    calls: list[list[str]] = []

    def invoke(args, *, timeout_seconds):  # type: ignore[no-untyped-def]
        del timeout_seconds
        calls.append(list(args))
        if args[0] == "run":
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="container-id\n",
                stderr="",
            )
        if args[0] == "exec":
            return subprocess.CompletedProcess(
                args,
                1,
                stdout="",
                stderr="venv initialization failed",
            )
        if args[0] == "logs":
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="PermissionError: TOKEN=secret",
                stderr="",
            )
        assert args[0] == "rm"
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(sandbox, "_invoke", invoke)

    with pytest.raises(DockerCommandError) as error:
        sandbox.create()

    message = str(error.value)
    assert "before command execution" in message
    assert "venv initialization failed" in message
    assert "PermissionError" in message
    assert "secret" not in message
    assert [call[0] for call in calls] == ["run", "exec", "logs", "rm"]
    assert calls[1][-1] == "/runtime/venv/bin/python"
    assert sandbox.container_id is None


def test_workspace_with_credentials_is_rejected(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("TOKEN=not-for-docker\n", encoding="utf-8")
    sandbox = DockerSandbox(
        SandboxConfig(workspace_path=tmp_path, run_id="unit-test"),
        docker_command=("docker",),
    )

    with pytest.raises(SandboxSecurityError):
        sandbox.create()


def test_execute_requires_an_owned_container(tmp_path: Path) -> None:
    sandbox = DockerSandbox(
        SandboxConfig(workspace_path=tmp_path, run_id="unit-test"),
        docker_command=("docker",),
    )

    with pytest.raises(SandboxError, match="must be created"):
        sandbox.execute("python -c \"print('no host fallback')\"")


def test_preferred_docker_command_is_preserved() -> None:
    assert resolve_docker_command(("wsl.exe", "-e", "docker")) == (
        "wsl.exe",
        "-e",
        "docker",
    )


@pytest.fixture(scope="session")
def docker_availability():
    try:
        return check_docker_available()
    except SandboxError as exc:
        pytest.skip(f"Docker integration unavailable: {exc}")


def _container_exists(command: list[str], container_id: str) -> bool:
    result = subprocess.run(
        [
            *command,
            "ps",
            "--all",
            "--filter",
            f"id={container_id}",
            "--format",
            "{{.ID}}",
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    return bool(result.stdout.strip())


def _assert_removed(command: list[str], container_id: str) -> None:
    assert not _container_exists(command, container_id)


@pytest.fixture
def docker_sandbox(tmp_path: Path, docker_availability):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = SandboxConfig(
        workspace_path=workspace,
        run_id=f"test-{tmp_path.name}-{uuid.uuid4().hex}",
        resource_limits=ResourceLimits(command_timeout_seconds=5),
    )
    sandbox = DockerSandbox(config, docker_command=docker_availability.command)
    try:
        sandbox.create()
        yield sandbox, docker_availability.command
    finally:
        sandbox.destroy()


@pytest.mark.docker
@pytest.mark.integration
def test_docker_success_and_cleanup(docker_sandbox) -> None:
    sandbox, command = docker_sandbox
    container_id = sandbox.container_id
    result = sandbox.execute("python -c \"print('hello')\"")

    assert container_id is not None
    assert result.exit_code == 0
    assert "hello" in result.stdout
    assert result.timed_out is False
    sandbox.destroy()
    _assert_removed(command, container_id)


@pytest.mark.docker
@pytest.mark.integration
def test_docker_captures_stderr(docker_sandbox) -> None:
    sandbox, command = docker_sandbox
    container_id = sandbox.container_id
    result = sandbox.execute("python -c \"import sys; sys.stderr.write('warning\\n')\"")

    assert container_id is not None
    assert result.exit_code == 0
    assert "warning" in result.stderr
    sandbox.destroy()
    _assert_removed(command, container_id)


@pytest.mark.docker
@pytest.mark.integration
def test_docker_preserves_nonzero_exit_code(docker_sandbox) -> None:
    sandbox, command = docker_sandbox
    container_id = sandbox.container_id
    result = sandbox.execute('python -c "raise SystemExit(7)"')

    assert container_id is not None
    assert result.exit_code == 7
    assert result.timed_out is False
    sandbox.destroy()
    _assert_removed(command, container_id)


@pytest.mark.docker
@pytest.mark.integration
def test_docker_timeout_removes_exact_container(docker_sandbox) -> None:
    sandbox, command = docker_sandbox
    container_id = sandbox.container_id
    result = sandbox.execute(
        'python -c "import time; time.sleep(30)"',
        timeout_seconds=1,
    )

    assert container_id is not None
    assert result.exit_code == 124
    assert result.timed_out is True
    assert result.cleanup_error is None
    assert sandbox.container_id is None
    _assert_removed(command, container_id)


@pytest.mark.docker
@pytest.mark.integration
def test_docker_mounts_workspace(docker_sandbox) -> None:
    sandbox, command = docker_sandbox
    container_id = sandbox.container_id
    file_path = sandbox.config.workspace_path / "fixture.txt"
    file_path.write_text("mounted content\n", encoding="utf-8")
    result = sandbox.execute(
        "python -c \"print(open('/workspace/fixture.txt').read().strip())\""
    )

    assert container_id is not None
    assert result.exit_code == 0
    assert "mounted content" in result.stdout
    sandbox.destroy()
    _assert_removed(command, container_id)


@pytest.mark.docker
@pytest.mark.integration
def test_docker_container_has_ownership_labels(docker_sandbox) -> None:
    sandbox, command = docker_sandbox
    container_id = sandbox.container_id
    inspect = subprocess.run(
        [
            *command,
            "inspect",
            "--format",
            '{{index .Config.Labels "reproscout.managed"}}',
            container_id,
        ],
        capture_output=True,
        check=True,
        text=True,
    )

    assert inspect.stdout.strip() == "true"
    sandbox.destroy()
    _assert_removed(command, container_id)


@pytest.mark.docker
@pytest.mark.integration
def test_docker_disables_network_by_default(docker_sandbox) -> None:
    sandbox, command = docker_sandbox
    container_id = sandbox.container_id
    result = sandbox.execute(
        (
            "python -c \"import urllib.request; "
            "urllib.request.urlopen('https://example.com', timeout=1)\""
        ),
        timeout_seconds=5,
    )

    assert sandbox.config.network == "none"
    assert result.exit_code != 0 or result.timed_out is True
    sandbox.destroy()
    _assert_removed(command, container_id)


@pytest.mark.docker
@pytest.mark.integration
def test_docker_pid_limit_contains_safe_process_abuse_fixture(docker_sandbox) -> None:
    sandbox, command = docker_sandbox
    container_id = sandbox.container_id
    source = (
        "import os\n"
        "children=[]\n"
        "for _ in range(256):\n"
        "    try:\n"
        "        pid=os.fork()\n"
        "    except OSError:\n"
        "        break\n"
        "    if pid == 0:\n"
        "        os._exit(0)\n"
        "    children.append(pid)\n"
        "for pid in children:\n"
        "    os.waitpid(pid, 0)\n"
        "print(len(children))\n"
    )
    encoded = base64.b64encode(source.encode()).decode()
    result = sandbox.execute(
        f"python -c \"import base64; exec(base64.b64decode('{encoded}'))\"",
        timeout_seconds=10,
    )

    assert result.exit_code == 0
    assert int(result.stdout.strip()) < 256
    sandbox.destroy()
    _assert_removed(command, container_id)
