"""Bounded deterministic failure classification and log normalization."""

from __future__ import annotations

import re
from collections.abc import Iterable

from reproscout.security import redact_sensitive_text

from .models import FailureClass, NormalizedFailure

_MAX_RAW_CHARACTERS = 200_000
_MAX_EXCERPT_CHARACTERS = 12_000
_MAX_LINES = 40

_RULES: tuple[tuple[FailureClass, tuple[str, ...]], ...] = (
    (
        FailureClass.PYTHON_VERSION_MISMATCH,
        (
            r"requires[- ]python",
            r"python (?:version )?[0-9.]+ is not supported",
            r"unsupported python version",
        ),
    ),
    (
        FailureClass.DEPENDENCY_CONFLICT,
        (
            r"resolutionimpossible",
            r"conflicting dependencies",
            r"dependency conflict",
            r"pip's dependency resolver",
        ),
    ),
    (
        FailureClass.MISSING_PACKAGE,
        (
            r"modulenotfounderror:\s*no module named",
            r"no matching distribution found for",
            r"could not find a version that satisfies the requirement",
        ),
    ),
    (
        FailureClass.IMPORT_ERROR,
        (r"importerror:", r"cannot import name .+ from"),
    ),
    (
        FailureClass.BUILD_TOOL_MISSING,
        (
            r"(?:cmake|ninja|gcc|g\+\+|make): (?:command )?not found",
            r"no (?:c|cxx) compiler could be found",
            r"unable to execute .+(?:gcc|clang|cl\.exe)",
        ),
    ),
    (
        FailureClass.SYSTEM_LIBRARY_MISSING,
        (
            r"cannot open shared object file",
            r"library not found for -l",
            r"cannot find -l[a-z0-9_-]+",
            r"dll load failed",
        ),
    ),
    (
        FailureClass.CUDA_MISMATCH,
        (
            r"cuda (?:driver|runtime|version).+(?:mismatch|insufficient|incompatible)",
            r"compiled with cuda.+but",
            r"invalid device function",
        ),
    ),
    (
        FailureClass.GPU_REQUIRED,
        (
            r"no nvidia driver",
            r"cuda is not available",
            r"gpu (?:is )?required",
            r"torch not compiled with cuda enabled",
        ),
    ),
    (
        FailureClass.OUT_OF_MEMORY,
        (r"out of memory", r"oom-kill", r"cannot allocate memory", r"\bkilled\b"),
    ),
    (
        FailureClass.CHECKPOINT_MISSING,
        (
            r"(?:checkpoint|weights?).+(?:not found|no such file)",
            r"no such file.+\.(?:ckpt|pth|pt|safetensors|bin)\b",
        ),
    ),
    (
        FailureClass.DATASET_MISSING,
        (
            r"dataset.+(?:not found|missing|does not exist)",
            r"no such file.+(?:data|dataset)",
        ),
    ),
    (
        FailureClass.FILE_NOT_FOUND,
        (r"filenotfounderror:", r"no such file or directory"),
    ),
    (
        FailureClass.DEAD_URL,
        (r"http(?: error)?\s*(?:404|410)\b", r"status code\s*(?:404|410)\b"),
    ),
    (
        FailureClass.AUTH_REQUIRED,
        (
            r"http(?: error)?\s*(?:401|403)\b",
            r"unauthorized",
            r"authentication (?:is )?required",
        ),
    ),
    (
        FailureClass.NETWORK_FAILURE,
        (
            r"connection (?:refused|reset|timed out)",
            r"temporary failure in name resolution",
            r"could not resolve host",
            r"network is unreachable",
            r"max retries exceeded with url",
        ),
    ),
    (
        FailureClass.PERMISSION_ERROR,
        (r"permissionerror:", r"permission denied", r"operation not permitted"),
    ),
    (
        FailureClass.CONFIG_ERROR,
        (
            r"(?:configuration|config) (?:error|invalid|missing)",
            r"yaml.+(?:scannererror|parsererror)",
            r"tomldecodeerror",
        ),
    ),
    (
        FailureClass.TEST_FAILURE,
        (
            r"=+ (?:short test summary info|failures) =+",
            r"\b[1-9][0-9]* failed(?:,| in|$)",
            r"pytest.+(?:failed|error)",
        ),
    ),
    (
        FailureClass.BUILD_FAILURE,
        (
            r"failed building wheel",
            r"error: command .+ failed with exit code",
            r"cmake error",
            r"compilation terminated",
            r"linker command failed",
        ),
    ),
)

_INTERESTING = re.compile(
    r"traceback|error|exception|failed|failure|warning|requires-python|"
    r"resolutionimpossible|no module named|not found|no such file|cuda|gpu|"
    r"out of memory|killed|permission|http|connection|cmake|compiler|assert",
    re.IGNORECASE,
)
_TRACEBACK_START = re.compile(r"^Traceback \(most recent call last\):")
_VOLATILE = re.compile(
    r"(?:/tmp/|\\tmp\\)[^\s:]+|0x[0-9a-f]+|\bpid[ =:]?[0-9]+\b",
    re.IGNORECASE,
)


def classify_failure(
    stdout: str,
    stderr: str,
    *,
    exit_code: int | None,
    timed_out: bool,
) -> FailureClass:
    """Classify only when deterministic evidence supports a category."""

    if timed_out or exit_code == 124:
        return FailureClass.TIMEOUT
    combined = f"{stdout}\n{stderr}"
    if exit_code in {137, 143}:
        return FailureClass.OUT_OF_MEMORY
    for failure_class, patterns in _RULES:
        if any(
            re.search(pattern, combined, re.IGNORECASE | re.MULTILINE)
            for pattern in patterns
        ):
            return failure_class
    return FailureClass.UNKNOWN


def _traceback_line_indexes(lines: list[str]) -> set[int]:
    selected: set[int] = set()
    for index, line in enumerate(lines):
        if not _TRACEBACK_START.search(line):
            continue
        for traceback_index in range(index, min(len(lines), index + 25)):
            selected.add(traceback_index)
            if traceback_index > index and re.match(
                r"^[A-Za-z_][\w.]+(?:Error|Exception):",
                lines[traceback_index].strip(),
            ):
                break
    return selected


def _select_lines(lines: list[str]) -> list[str]:
    indexes = _traceback_line_indexes(lines)
    for index, line in enumerate(lines):
        if _INTERESTING.search(line):
            indexes.update(range(max(0, index - 1), min(len(lines), index + 2)))
    if not indexes:
        indexes.update(range(min(8, len(lines))))
        indexes.update(range(max(0, len(lines) - 8), len(lines)))
    selected = [lines[index].rstrip() for index in sorted(indexes)]
    return [line for line in selected if line.strip()][:_MAX_LINES]


def _bounded_lines(lines: Iterable[str]) -> tuple[list[str], bool]:
    selected: list[str] = []
    count = 0
    truncated = False
    for line in lines:
        cleaned = _VOLATILE.sub("<volatile>", redact_sensitive_text(line))
        if len(cleaned) > 1_000:
            cleaned = cleaned[:985] + "...[truncated]"
            truncated = True
        if count + len(cleaned) > _MAX_EXCERPT_CHARACTERS:
            truncated = True
            break
        selected.append(cleaned)
        count += len(cleaned)
    return selected, truncated


def normalize_failure(
    stdout: str,
    stderr: str,
    *,
    exit_code: int | None,
    timed_out: bool,
    log_reference: str | None = None,
) -> NormalizedFailure:
    """Build a bounded excerpt while retaining a reference to full run logs."""

    raw = f"{stdout}\n{stderr}"
    raw_count = len(raw)
    bounded_raw = raw[:_MAX_RAW_CHARACTERS]
    raw_lines = bounded_raw.splitlines()
    candidate_lines = _select_lines(raw_lines)
    selected, excerpt_truncated = _bounded_lines(candidate_lines)
    failure_class = classify_failure(
        stdout,
        stderr,
        exit_code=exit_code,
        timed_out=timed_out,
    )
    headline = next(
        (line for line in reversed(selected) if _INTERESTING.search(line)),
        selected[-1] if selected else f"Command failed with exit code {exit_code}.",
    )
    excerpt_count = sum(len(line) for line in selected)
    return NormalizedFailure(
        failure_class=failure_class,
        headline=headline,
        important_lines=selected,
        log_reference=log_reference,
        exit_code=exit_code,
        timed_out=timed_out,
        raw_character_count=raw_count,
        excerpt_character_count=excerpt_count,
        truncated=(
            raw_count > _MAX_RAW_CHARACTERS
            or excerpt_truncated
            or len(candidate_lines) > len(selected)
            or len(candidate_lines) < sum(bool(line.strip()) for line in raw_lines)
        ),
    )
