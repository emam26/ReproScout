"""Secret-free environment fingerprint collection inside a sandbox."""

from __future__ import annotations

import json
import re

from reproscout.sandbox import Sandbox, SandboxConfig

from .models import EnvironmentFingerprint, EnvironmentPackage


class EnvironmentFingerprintError(RuntimeError):
    """Raised when sandbox fingerprint evidence is unavailable or malformed."""


_MAX_FINGERPRINT_CHARACTERS = 2_000_000
_SENSITIVE_ENVIRONMENT_NAME = re.compile(
    r"(?:key|token|secret|password|passwd|authorization|credential|private)",
    re.IGNORECASE,
)
_FINGERPRINT_SCRIPT = """
import importlib.metadata
import json
import os
import platform
from pathlib import Path

packages = sorted(
    ({"name": item.metadata.get("Name") or item.name, "version": item.version}
     for item in importlib.metadata.distributions()),
    key=lambda item: (item["name"].lower(), item["version"]),
)
payload = {
    "os_name": platform.system(),
    "os_release": platform.release(),
    "architecture": platform.machine(),
    "python_version": platform.python_version(),
    "pip_version": next((item["version"] for item in packages
                         if item["name"].lower() == "pip"), None),
    "installed_packages": packages,
    "gpu_visible": any(Path("/dev").glob("nvidia*")),
    "cuda_runtime": None,
    "compiler": platform.python_compiler(),
    "environment_variable_names": sorted(os.environ),
}
print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
""".strip()


def parse_environment_fingerprint(
    value: str,
    *,
    repository_commit_sha: str,
    config: SandboxConfig,
    container_image_digest: str | None = None,
) -> EnvironmentFingerprint:
    """Validate bounded JSON produced by the in-container fingerprint script."""

    if len(value) > _MAX_FINGERPRINT_CHARACTERS:
        raise EnvironmentFingerprintError("Environment fingerprint exceeds size limit.")
    try:
        data = json.loads(value)
    except json.JSONDecodeError as exc:
        raise EnvironmentFingerprintError(
            "Environment fingerprint is not valid JSON."
        ) from exc
    if not isinstance(data, dict):
        raise EnvironmentFingerprintError("Environment fingerprint must be an object.")

    package_value = data.get("installed_packages")
    packages: list[EnvironmentPackage] = []
    if isinstance(package_value, list):
        for item in package_value[:5_000]:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            version = item.get("version")
            if isinstance(name, str) and name.strip() and isinstance(version, str):
                packages.append(
                    EnvironmentPackage(name=name, version=version or "unknown")
                )

    names_value = data.get("environment_variable_names")
    safe_names = (
        sorted(
            {
                name
                for name in names_value
                if isinstance(name, str)
                and name
                and not _SENSITIVE_ENVIRONMENT_NAME.search(name)
            }
        )[:2_000]
        if isinstance(names_value, list)
        else []
    )

    def text_field(name: str, *, required: bool = True) -> str | None:
        result = data.get(name)
        if isinstance(result, str) and (result or not required):
            return result
        if required:
            raise EnvironmentFingerprintError(f"Environment fingerprint lacks {name}.")
        return None

    gpu_value = data.get("gpu_visible")
    if not isinstance(gpu_value, bool):
        raise EnvironmentFingerprintError("Environment fingerprint lacks gpu_visible.")

    return EnvironmentFingerprint(
        repository_commit_sha=repository_commit_sha,
        container_image=config.image,
        container_image_digest=container_image_digest,
        os_name=text_field("os_name") or "unknown",
        os_release=text_field("os_release", required=False) or "",
        architecture=text_field("architecture") or "unknown",
        python_version=text_field("python_version") or "unknown",
        pip_version=text_field("pip_version", required=False),
        installed_packages=packages,
        cpu_allocation=config.resource_limits.cpus,
        memory_limit=config.resource_limits.memory,
        gpu_visible=gpu_value,
        cuda_runtime=text_field("cuda_runtime", required=False),
        compiler=text_field("compiler", required=False),
        environment_variable_names=safe_names,
        network_mode=config.network,
    )


def collect_environment_fingerprint(
    sandbox: Sandbox,
    *,
    repository_commit_sha: str,
    config: SandboxConfig,
    container_image_digest: str | None = None,
    timeout_seconds: float = 30.0,
) -> EnvironmentFingerprint:
    """Collect environment facts by executing only inside the Docker abstraction."""

    result = sandbox.execute(
        ["python", "-c", _FINGERPRINT_SCRIPT],
        timeout_seconds=timeout_seconds,
    )
    if result.exit_code != 0 or result.timed_out:
        raise EnvironmentFingerprintError(
            "Sandbox environment fingerprint command did not complete successfully."
        )
    return parse_environment_fingerprint(
        result.stdout,
        repository_commit_sha=repository_commit_sha,
        config=config,
        container_image_digest=container_image_digest,
    )
