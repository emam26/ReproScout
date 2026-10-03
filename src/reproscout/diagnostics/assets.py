"""Bounded repository-local asset, LFS, submodule, and DVC detection."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .models import AssetKind, AssetReference, RepositoryAssets

_URL = re.compile(r"https?://[^\s)'\"<>]+", re.IGNORECASE)
_CHECKPOINT = re.compile(
    r"(?:checkpoint|weights?|model).*(?:\.ckpt|\.pth|\.pt|\.safetensors|\.bin)$|"
    r"\.(?:ckpt|pth|pt|safetensors)$",
    re.IGNORECASE,
)
_DATASET = re.compile(
    r"(?:^|/)(?:data|dataset|datasets)(?:/|$)|\.(?:csv|parquet|arrow|tfrecord)$",
    re.IGNORECASE,
)
_LFS_HEADER = "version https://git-lfs.github.com/spec/v1"


@dataclass(frozen=True, slots=True)
class AssetScanLimits:
    max_files: int = 5_000
    max_file_bytes: int = 256 * 1024
    max_references: int = 1_000

    def __post_init__(self) -> None:
        if self.max_files < 1 or self.max_file_bytes < 1 or self.max_references < 1:
            raise ValueError("Asset scan limits must be positive.")


def _safe_url(value: str) -> str | None:
    parsed = urlsplit(value.rstrip(".,;"))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def detect_repository_assets(
    workspace: Path,
    limits: AssetScanLimits | None = None,
) -> RepositoryAssets:
    """Detect indicators only; never download or probe external resources."""

    policy = limits or AssetScanLimits()
    root = Path(workspace).resolve()
    references: set[tuple[AssetKind, str, str]] = set()
    scanned_files = 0
    truncated = False

    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        directories[:] = sorted(
            directory
            for directory in directories
            if directory != ".git" and not (Path(current) / directory).is_symlink()
        )
        for filename in sorted(files):
            if (
                scanned_files >= policy.max_files
                or len(references) >= policy.max_references
            ):
                truncated = True
                break
            path = Path(current) / filename
            if path.is_symlink():
                continue
            try:
                relative = path.resolve().relative_to(root).as_posix()
                size = path.stat().st_size
            except (OSError, ValueError):
                continue
            scanned_files += 1

            lowered = relative.lower()
            if _CHECKPOINT.search(lowered):
                references.add((AssetKind.CHECKPOINT, relative, relative))
            if _DATASET.search(lowered):
                references.add((AssetKind.DATASET, relative, relative))
            if filename == ".gitmodules":
                references.add((AssetKind.GIT_SUBMODULE, relative, relative))
            if (
                filename == "dvc.yaml"
                or filename.endswith(".dvc")
                or Path(relative).parts[0] == ".dvc"
            ):
                references.add((AssetKind.DVC, relative, relative))
            if size > policy.max_file_bytes:
                continue
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw:
                continue
            text = raw.decode("utf-8", errors="replace")
            if text.startswith(_LFS_HEADER):
                references.add((AssetKind.GIT_LFS_POINTER, relative, relative))
            for match in _URL.finditer(text):
                url = _safe_url(match.group(0))
                if url is not None:
                    references.add((AssetKind.EXTERNAL_URL, url, relative))
                if len(references) >= policy.max_references:
                    truncated = True
                    break
        if truncated:
            break

    return RepositoryAssets(
        references=[
            AssetReference(kind=kind, value=value, source_path=source)
            for kind, value, source in sorted(
                references,
                key=lambda item: (item[0].value, item[2], item[1]),
            )
        ],
        scanned_files=scanned_files,
        truncated=truncated,
    )
