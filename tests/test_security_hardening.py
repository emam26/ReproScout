from __future__ import annotations

from pathlib import Path

import pytest

from reproscout.network import (
    PublicUrlError,
    SafeDownloadLimits,
    download_public_url,
    validate_public_url,
)
from reproscout.repo import clone as clone_module
from reproscout.sandbox import (
    DockerSandbox,
    ResourceLimits,
    SandboxConfig,
    SandboxError,
)


def test_public_url_policy_rejects_private_destinations_credentials_and_queries() -> (
    None
):
    with pytest.raises(PublicUrlError):
        validate_public_url("https://127.0.0.1/model.bin")
    with pytest.raises(PublicUrlError):
        validate_public_url("https://user:password@example.org/model.bin")
    with pytest.raises(PublicUrlError):
        validate_public_url("https://example.org/model.bin?token=hidden")
    with pytest.raises(PublicUrlError):
        validate_public_url(
            "https://public.example/model.bin",
            resolve=True,
            resolver=lambda _host, _port: ["10.0.0.4"],
        )

    assert validate_public_url("https://example.org/model.bin") == (
        "https://example.org/model.bin"
    )


class _FakeResponse:
    def __init__(
        self, status: int, *, headers: dict[str, str], content: bytes = b""
    ) -> None:
        self.status = status
        self.headers = headers
        self.content = content
        self.offset = 0
        self.closed = False

    def getcode(self) -> int:
        return self.status

    def read(self, size: int) -> bytes:
        chunk = self.content[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class _FakeOpener:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.responses = responses
        self.urls: list[str] = []

    def open(self, request, *, timeout: float):  # type: ignore[no-untyped-def]
        del timeout
        self.urls.append(request.full_url)
        return self.responses.pop(0)


def _public_resolver(host: str, _port: int) -> list[str]:
    if host in {"example.org", "next.example.org"}:
        return ["93.184.216.34"]
    return ["127.0.0.1"]


def test_download_revalidates_redirects_and_enforces_byte_limits() -> None:
    opener = _FakeOpener(
        [
            _FakeResponse(
                302,
                headers={"Location": "https://next.example.org/model.bin"},
            ),
            _FakeResponse(200, headers={"Content-Length": "2"}, content=b"ok"),
        ]
    )
    result = download_public_url(
        "https://example.org/model.bin",
        resolver=_public_resolver,
        opener=opener,
    )
    assert result.content == b"ok"
    assert result.redirects == 1
    assert opener.urls == [
        "https://example.org/model.bin",
        "https://next.example.org/model.bin",
    ]

    with pytest.raises(PublicUrlError, match="byte limit"):
        download_public_url(
            "https://example.org/model.bin",
            resolver=_public_resolver,
            opener=_FakeOpener(
                [_FakeResponse(200, headers={"Content-Length": "5"}, content=b"12345")]
            ),
            limits=SafeDownloadLimits(max_bytes=4),
        )

    with pytest.raises(PublicUrlError):
        download_public_url(
            "https://example.org/model.bin",
            resolver=_public_resolver,
            opener=_FakeOpener(
                [
                    _FakeResponse(
                        302, headers={"Location": "https://private.example/model"}
                    )
                ]
            ),
        )


def test_git_intake_uses_sterile_noninteractive_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((args, kwargs["env"]))
        if "rev-parse" in args:
            return clone_module.subprocess.CompletedProcess(
                args, 0, stdout="a" * 40 + "\n", stderr=""
            )
        if "symbolic-ref" in args:
            return clone_module.subprocess.CompletedProcess(
                args, 1, stdout="", stderr=""
            )
        return clone_module.subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(clone_module.subprocess, "run", fake_run)
    result = clone_module.clone_repository(
        "https://github.com/example/project",
        tmp_path / "repository",
    )

    clone_args = calls[0][0]
    environment = calls[0][1]
    assert result.commit_sha == "a" * 40
    assert "--no-recurse-submodules" in clone_args
    assert "GIT_TERMINAL_PROMPT" in environment
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["GIT_LFS_SKIP_SMUDGE"] == "1"
    assert "SSH_AUTH_SOCK" not in environment


def test_sandbox_defaults_to_no_network_and_bounds_workspace(tmp_path: Path) -> None:
    assert SandboxConfig(workspace_path=tmp_path, run_id="safe").network == "none"
    (tmp_path / "large.bin").write_bytes(b"x" * 1_025)
    sandbox = DockerSandbox(
        SandboxConfig(
            workspace_path=tmp_path,
            run_id="safe",
            resource_limits=ResourceLimits(max_workspace_bytes=1_024),
        ),
        docker_command=("docker",),
    )

    with pytest.raises(SandboxError, match="byte-size"):
        sandbox._validate_workspace()
