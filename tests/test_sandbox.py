from __future__ import annotations

import base64
import subprocess
from pathlib import Path

import pytest

from reproscout.sandbox import (
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

    arguments = sandbox._build_run_args()

    assert "--privileged" not in arguments
    assert "--network=host" not in arguments
    assert "/var/run/docker.sock" not in " ".join(arguments)
    assert "--cap-drop=ALL" in arguments
    assert "--security-opt=no-new-privileges" in arguments
    assert "--memory=512m" in arguments
    assert "--cpus=1" in arguments
    assert "--pids-limit=128" in arguments
    assert "reproscout.managed=true" in arguments
    assert "reproscout.run_id=unit-test" in arguments
    assert any("target=/workspace" in argument for argument in arguments)


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
        run_id=f"test-{tmp_path.name}",
        resource_limits=ResourceLimits(command_timeout_seconds=5),
    )
    sandbox = DockerSandbox(config, docker_command=docker_availability.command)
    sandbox.create()
    try:
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
        "python -c \"import urllib.request; urllib.request.urlopen('https://example.com', timeout=1)\"",
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
