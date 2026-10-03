"""Workspace-confined, reversible file repairs for untrusted target repositories."""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from pydantic import Field

from reproscout.diagnostics.models import DiagnosticModel
from reproscout.security import (
    BoundedReadError,
    read_bounded_workspace_file,
    redact_sensitive_text,
)

from .models import RepairAction, RepairActionType
from .plan import RepairNotExecutableError


class WorkspaceEditError(ValueError):
    """Raised when a workspace edit violates a safety or consistency bound."""


class WorkspaceEditConflictError(WorkspaceEditError):
    """Raised when a file changed after the editor captured its version."""


class WorkspaceEditLimits(DiagnosticModel):
    """Hard bounds for one in-memory workspace edit transaction."""

    max_file_bytes: int = Field(default=1_000_000, ge=1_024, le=100_000_000)
    max_patch_characters: int = Field(default=4_000, ge=100, le=1_000_000)
    max_changed_files: int = Field(default=8, ge=1, le=100)
    max_changed_bytes: int = Field(default=4_000_000, ge=1_024, le=500_000_000)


class WorkspaceFile(DiagnosticModel):
    """A bounded text read with its objective content digest."""

    path: str
    content: str
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class FileDigest(DiagnosticModel):
    """The immutable identity of one file version."""

    path: str
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class WorkspaceChange(DiagnosticModel):
    """One exact before/after file change and its unified diff."""

    path: str
    before: FileDigest
    after: FileDigest
    diff: str = Field(min_length=1, max_length=1_000_000)


class WorkspaceEditResult(DiagnosticModel):
    """Auditable result of one or more bounded workspace operations."""

    operation: str = Field(min_length=1, max_length=100)
    changes: list[WorkspaceChange] = Field(min_length=1, max_length=100)
    patches_diff: str = Field(min_length=1, max_length=1_000_000)


_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?\r?\n?$")


def _digest(path: str, content: bytes) -> FileDigest:
    return FileDigest(
        path=path,
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _reject_sensitive_text(value: str) -> None:
    if redact_sensitive_text(value) != value:
        raise WorkspaceEditError("Workspace repair contains credential-like material.")


@dataclass(slots=True)
class _RecordedChange:
    original: bytes
    current: bytes


class WorkspaceEditor:
    """Apply only bounded edits beneath one explicitly selected workspace."""

    def __init__(
        self,
        workspace: Path,
        *,
        limits: WorkspaceEditLimits | None = None,
        audit_directory: Path | None = None,
    ) -> None:
        root = Path(workspace).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise WorkspaceEditError("Workspace root must be an existing directory.")
        project_root = Path(__file__).resolve().parents[3]
        if root == project_root:
            raise WorkspaceEditError(
                "ReproScout's own project root cannot be a target repair workspace."
            )
        self.root = root
        self.limits = limits or WorkspaceEditLimits()
        self.audit_directory = (
            Path(audit_directory).expanduser().resolve() if audit_directory else None
        )
        if self.audit_directory is not None:
            self.audit_directory.mkdir(parents=True, exist_ok=True)
        self._changes: dict[str, _RecordedChange] = {}

    @property
    def changed_files(self) -> tuple[str, ...]:
        return tuple(sorted(self._changes))

    def read_file(self, relative_path: str) -> WorkspaceFile:
        normalized, target = self._target(relative_path)
        content = self._read_existing(normalized, target)
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceEditError(
                "Workspace editor only supports UTF-8 text files."
            ) from exc
        return WorkspaceFile(
            path=normalized,
            content=text,
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )

    def replace_text(
        self,
        relative_path: str,
        old: str,
        new: str,
        *,
        expected_sha256: str | None = None,
    ) -> WorkspaceEditResult:
        """Replace exactly one text occurrence after an optional hash check."""

        if not old:
            raise WorkspaceEditError("Replacement target cannot be empty.")
        _reject_sensitive_text(new)
        normalized, target = self._target(relative_path)
        before = self._read_existing(normalized, target)
        before_hash = hashlib.sha256(before).hexdigest()
        if expected_sha256 is not None and before_hash != expected_sha256:
            raise WorkspaceEditConflictError("File hash changed before replacement.")
        try:
            text = before.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceEditError(
                "Workspace editor only supports UTF-8 text files."
            ) from exc
        if text.count(old) != 1:
            raise WorkspaceEditError("Replacement must match exactly one occurrence.")
        after = text.replace(old, new, 1).encode("utf-8")
        self._apply_bytes(normalized, target, before, after)
        return self.result("replace_text")

    def apply_patch(self, patch: str) -> WorkspaceEditResult:
        """Apply a bounded standard unified diff without invoking a shell or Git."""

        if (
            not isinstance(patch, str)
            or not patch
            or len(patch) > self.limits.max_patch_characters
        ):
            raise WorkspaceEditError("Patch is empty or exceeds its configured bound.")
        if "\x00" in patch or "--- /" in patch or "+++ /" in patch:
            raise WorkspaceEditError("Patch contains unsafe absolute paths.")
        _reject_sensitive_text(patch)
        changes = self._parse_patch(patch)
        if not changes:
            raise WorkspaceEditError("Patch contains no supported file changes.")
        if len({path for path, _, _ in changes}) != len(changes):
            raise WorkspaceEditError("Patch cannot modify one file more than once.")
        if len(self._changes) + len(changes) > self.limits.max_changed_files:
            raise WorkspaceEditError("Patch exceeds the changed-file limit.")
        for normalized, before, after in changes:
            _, target = self._target(normalized)
            current = self._read_existing(normalized, target)
            if current != before:
                raise WorkspaceEditConflictError(
                    f"Patch source for {normalized} does not match the workspace."
                )
        for normalized, before, after in changes:
            _, target = self._target(normalized)
            self._apply_bytes(normalized, target, before, after)
        return self.result("apply_patch")

    def result(self, operation: str = "workspace_edit") -> WorkspaceEditResult:
        """Return the current exact diff without changing the workspace."""

        if not self._changes:
            raise WorkspaceEditError("No workspace changes are available.")
        changes: list[WorkspaceChange] = []
        for normalized in self.changed_files:
            record = self._changes[normalized]
            diff = self._diff(normalized, record.original, record.current)
            changes.append(
                WorkspaceChange(
                    path=normalized,
                    before=_digest(normalized, record.original),
                    after=_digest(normalized, record.current),
                    diff=diff,
                )
            )
        patches_diff = "".join(change.diff for change in changes)
        return WorkspaceEditResult(
            operation=operation,
            changes=changes,
            patches_diff=patches_diff,
        )

    def write_patch_artifact(self) -> Path:
        """Persist the exact diff only beneath the explicitly supplied run directory."""

        if self.audit_directory is None:
            raise WorkspaceEditError("An audit directory is required for patches.diff.")
        result = self.result()
        target = self.audit_directory / "patches.diff"
        self._ensure_inside(self.audit_directory, target)
        self._atomic_write(target, result.patches_diff.encode("utf-8"))
        return target

    def rollback(self) -> None:
        """Restore every edited file only if no external process changed it."""

        for normalized in self.changed_files:
            record = self._changes[normalized]
            _, target = self._target(normalized)
            current = self._read_existing(normalized, target)
            if current != record.current:
                raise WorkspaceEditConflictError(
                    f"Cannot roll back {normalized}: current content changed."
                )
        for normalized in reversed(self.changed_files):
            record = self._changes[normalized]
            _, target = self._target(normalized)
            self._atomic_write(target, record.original)
        self._changes.clear()

    def _target(self, relative_path: str) -> tuple[str, Path]:
        if (
            not isinstance(relative_path, str)
            or not relative_path
            or "\x00" in relative_path
        ):
            raise WorkspaceEditError("Workspace path must be a non-empty string.")
        normalized = relative_path.replace("\\", "/")
        posix = PurePosixPath(normalized)
        windows = PureWindowsPath(relative_path)
        if (
            posix.is_absolute()
            or windows.is_absolute()
            or any(part in {"", ".", ".."} for part in posix.parts)
            or posix.parts[0] == ".git"
        ):
            raise WorkspaceEditError(
                "Workspace path must remain inside the target workspace."
            )
        target = self.root.joinpath(*posix.parts)
        resolved = target.resolve(strict=False)
        self._ensure_inside(self.root, resolved)
        current = self.root
        for part in posix.parts:
            current /= part
            if current.is_symlink():
                raise WorkspaceEditError("Symlink paths are not editable.")
        return normalized, target

    def _read_existing(self, normalized: str, target: Path) -> bytes:
        try:
            content = read_bounded_workspace_file(
                self.root,
                normalized,
                max_bytes=self.limits.max_file_bytes,
            )
        except BoundedReadError as exc:
            if "exceeds the read limit" in str(exc):
                raise WorkspaceEditError(
                    "Workspace file exceeds the configured size limit."
                ) from exc
            raise WorkspaceEditError(
                f"Could not read workspace file: {normalized}"
            ) from exc
        return content

    def _apply_bytes(
        self,
        normalized: str,
        target: Path,
        before: bytes,
        after: bytes,
    ) -> None:
        if len(after) > self.limits.max_file_bytes:
            raise WorkspaceEditError("Edited file exceeds the configured size limit.")
        if (
            normalized not in self._changes
            and len(self._changes) >= self.limits.max_changed_files
        ):
            raise WorkspaceEditError("Edit exceeds the changed-file limit.")
        existing = self._changes.get(normalized)
        original = before if existing is None else existing.original
        total = sum(len(item.current) for item in self._changes.values())
        total -= len(existing.current) if existing is not None else 0
        total += len(after)
        if total > self.limits.max_changed_bytes:
            raise WorkspaceEditError("Edit exceeds the changed-byte limit.")
        diff = self._diff(normalized, original, after)
        _reject_sensitive_text(diff)
        _reject_sensitive_text(after.decode("utf-8", errors="replace"))
        self._atomic_write(target, after)
        if after == original:
            self._changes.pop(normalized, None)
        else:
            self._changes[normalized] = _RecordedChange(
                original=original, current=after
            )

    def _parse_patch(self, patch: str) -> list[tuple[str, bytes, bytes]]:
        lines = patch.splitlines(keepends=True)
        parsed: list[tuple[str, bytes, bytes]] = []
        index = 0
        while index < len(lines):
            if not lines[index].startswith("--- "):
                index += 1
                continue
            old_path = self._patch_path(lines[index][4:])
            index += 1
            if index >= len(lines) or not lines[index].startswith("+++ "):
                raise WorkspaceEditError(
                    "Unified patch is missing its new-file header."
                )
            new_path = self._patch_path(lines[index][4:])
            index += 1
            if old_path != new_path:
                raise WorkspaceEditError(
                    "File rename and create/delete patches are not allowed."
                )
            normalized, target = self._target(old_path)
            before = self._read_existing(normalized, target)
            try:
                text = before.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise WorkspaceEditError(
                    "Workspace editor only supports UTF-8 text files."
                ) from exc
            old_lines = text.splitlines(keepends=True)
            newline = "\r\n" if "\r\n" in text else "\n"
            new_lines: list[str] = []
            cursor = 0
            saw_hunk = False
            while index < len(lines) and not lines[index].startswith("--- "):
                match = _HUNK.match(lines[index])
                if match is None:
                    index += 1
                    continue
                saw_hunk = True
                old_start = int(match.group(1))
                old_count = int(match.group(2) or "1")
                new_count = int(match.group(4) or "1")
                hunk_lines: list[str] = []
                index += 1
                while index < len(lines):
                    line = lines[index]
                    if line.startswith(("@@ ", "--- ")):
                        break
                    if line.startswith("\\ No newline"):
                        index += 1
                        continue
                    if not line or line[0] not in {" ", "+", "-"}:
                        raise WorkspaceEditError(
                            "Unified patch contains an invalid hunk line."
                        )
                    hunk_lines.append(line)
                    index += 1
                expected_cursor = max(old_start - 1, 0)
                if expected_cursor < cursor or expected_cursor > len(old_lines):
                    raise WorkspaceEditError("Unified patch hunk location is invalid.")
                new_lines.extend(old_lines[cursor:expected_cursor])
                consumed_old = 0
                produced_new = 0
                cursor = expected_cursor
                for line in hunk_lines:
                    marker, content = line[0], line[1:]
                    if marker in {" ", "-"}:
                        if cursor >= len(old_lines) or old_lines[cursor].replace(
                            "\r\n", "\n"
                        ) != content.replace("\r\n", "\n"):
                            raise WorkspaceEditConflictError(
                                f"Patch context does not match {normalized}."
                            )
                        if marker == " ":
                            new_lines.append(old_lines[cursor])
                        cursor += 1
                        consumed_old += 1
                    if marker == "+":
                        new_lines.append(
                            content
                            if newline == "\n"
                            else content.replace("\n", "\r\n")
                        )
                        produced_new += 1
                if consumed_old != old_count or produced_new != new_count:
                    raise WorkspaceEditError(
                        "Unified patch hunk counts are inconsistent."
                    )
            if not saw_hunk:
                raise WorkspaceEditError("Unified patch file section has no hunks.")
            new_lines.extend(old_lines[cursor:])
            after = "".join(new_lines).encode("utf-8")
            parsed.append((normalized, before, after))
        return parsed

    @staticmethod
    def _patch_path(value: str) -> str:
        path = value.rstrip("\r\n").split("\t", 1)[0]
        if path.startswith(("a/", "b/")):
            path = path[2:]
        if path == "/dev/null" or not path:
            raise WorkspaceEditError("Patch creation and deletion are not supported.")
        return path

    @staticmethod
    def _diff(path: str, before: bytes, after: bytes) -> str:
        old = before.decode("utf-8").splitlines(keepends=True)
        new = after.decode("utf-8").splitlines(keepends=True)
        return "".join(
            difflib.unified_diff(
                old,
                new,
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
        )

    @staticmethod
    def _ensure_inside(root: Path, target: Path) -> None:
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise WorkspaceEditError("Path escapes the configured workspace.") from exc

    @staticmethod
    def _atomic_write(target: Path, content: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            original_mode = target.stat().st_mode & 0o777 if target.exists() else None
        except OSError as exc:
            raise WorkspaceEditError("Could not inspect workspace file mode.") from exc
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".reproscout.tmp",
                delete=False,
            ) as stream:
                temporary = stream.name
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if original_mode is not None:
                os.chmod(temporary, original_mode)
            os.replace(temporary, target)
        except OSError as exc:
            raise WorkspaceEditError(
                f"Could not write workspace file: {target.name}"
            ) from exc
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass


@dataclass(slots=True)
class AppliedWorkspaceRepair:
    """Workspace mutation plus the exact rollback operation."""

    editor: WorkspaceEditor
    edit: WorkspaceEditResult

    def rollback(self) -> WorkspaceEditResult:
        self.editor.rollback()
        return self.edit


class WorkspaceRepairApplier:
    """Adapt the Phase 9 patch action to the controlled workspace editor."""

    def __init__(self, editor: WorkspaceEditor) -> None:
        self.editor = editor

    def apply(self, action: RepairAction) -> AppliedWorkspaceRepair:
        if action.action_type is not RepairActionType.APPLY_MINIMAL_PATCH:
            raise RepairNotExecutableError(
                f"{action.action_type.value} requires a later workspace tool."
            )
        patch = action.arguments.get("patch")
        if not isinstance(patch, str):
            raise WorkspaceEditError("Minimal patch action requires a text patch.")
        edit = self.editor.apply_patch(patch)
        try:
            self.editor.write_patch_artifact()
        except Exception:
            self.editor.rollback()
            raise
        return AppliedWorkspaceRepair(self.editor, edit)
