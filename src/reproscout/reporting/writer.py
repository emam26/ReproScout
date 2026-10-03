"""Secret-safe, bounded report and run-artifact writing."""

from __future__ import annotations

import json
import os
from pathlib import Path

from reproscout.execution import StepExecutionResult
from reproscout.planning.safety import PlanSafetyError, validate_command
from reproscout.security import redact_sensitive_text, reject_sensitive_mapping
from reproscout.state import Event

from .models import ReportArtifactPaths, RunReport


class ReportError(ValueError):
    """Raised when a report artifact cannot be written safely."""


class RunReportWriter:
    """Write only supplied real data beneath one run artifact directory."""

    def __init__(self, run_directory: Path) -> None:
        root = Path(run_directory).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        if not root.is_dir():
            raise ReportError("Run artifact directory is not a directory.")
        self.root = root

    def write(
        self,
        report: RunReport,
        *,
        events: list[Event] | None = None,
        commands: list[StepExecutionResult] | None = None,
        environment: object | None = None,
        patches_diff: str | None = None,
        recipe_commands: list[str] | None = None,
    ) -> ReportArtifactPaths:
        written = ["run.json", "report.md"]
        self._write_text("run.json", self._safe_json(report.model_dump(mode="json")))
        self._write_text("report.md", self._markdown(report))
        if events:
            self._write_json_lines(
                "events.jsonl",
                [self._event_dict(event) for event in events],
            )
            written.append("events.jsonl")
        if commands:
            self._write_json_lines(
                "commands.jsonl",
                [command.model_dump(mode="json") for command in commands],
            )
            written.append("commands.jsonl")
        if environment is not None:
            model_dump = getattr(environment, "model_dump", None)
            if model_dump is None:
                raise ReportError("Environment artifact requires a typed model.")
            self._write_text(
                "environment.json",
                self._safe_json(model_dump(mode="json")),
            )
            written.append("environment.json")
        if patches_diff:
            self._safe_text(patches_diff)
            self._write_text("patches.diff", patches_diff)
            written.append("patches.diff")
        if recipe_commands:
            self._write_recipe(recipe_commands)
            written.append("reproduce.sh")
        return ReportArtifactPaths(run_directory=str(self.root), written_files=written)

    def _write_recipe(self, commands: list[str]) -> None:
        safe_commands: list[str] = []
        for command in commands:
            if (
                not isinstance(command, str)
                or not command.strip()
                or "\r" in command
                or "\n" in command
            ):
                raise ReportError("Reproduction recipe contains an invalid command.")
            try:
                validate_command(command, max_length=2_000)
            except PlanSafetyError as exc:
                raise ReportError(str(exc)) from exc
            self._safe_text(command)
            safe_commands.append(command)
        content = "#!/usr/bin/env bash\nset -eu\n\n" + "\n".join(safe_commands) + "\n"
        self._write_text("reproduce.sh", content)
        try:
            os.chmod(self.root / "reproduce.sh", 0o700)
        except OSError:
            pass

    def _write_json_lines(self, relative: str, values: list[object]) -> None:
        lines = [self._safe_json(value) for value in values]
        self._write_text(relative, "".join(f"{line}\n" for line in lines))

    @staticmethod
    def _event_dict(event: Event) -> dict[str, object]:
        return {
            "event_type": event.event_type.value,
            "payload": event.payload,
            "run_id": event.run_id,
            "sequence": event.sequence,
            "stage": event.stage.value,
            "timestamp": event.timestamp.isoformat(),
        }

    def _markdown(self, report: RunReport) -> str:
        lines = [
            "# Reproducibility Audit Report",
            "",
            f"- Schema version: `{report.schema_version}`",
            f"- Run: `{report.run_id}`",
            f"- Repository: `{report.repository}`",
            f"- Commit: `{report.commit_sha}`",
            f"- Goal: {report.goal}",
            "",
            "## Official documented reproduction",
            "",
            f"- Attempt: `{report.initial_attempt.attempt_id}`",
            f"- Description: {report.initial_attempt.description}",
            f"- Workflow succeeded: {report.initial_attempt.workflow_succeeded}",
        ]
        if report.documented_setup:
            lines.extend(
                [
                    "- Documented setup:",
                    *[f"  - {item}" for item in report.documented_setup],
                ]
            )
        lines.extend(["", "## Planned actions and provenance", ""])
        if report.plan_steps:
            for step in report.plan_steps:
                command = step.command or "(no command)"
                source = step.source_path or "no source location"
                if step.source_location:
                    source += f":{step.source_location}"
                lines.append(
                    f"- `{step.step_id}` `{step.action_type.value}` `{command}` "
                    f"— `{step.provenance.value}`, source: `{source}`"
                )
        else:
            lines.append("- No plan provenance was supplied.")
        lines.extend(["", "## Agent-assisted reproduction", ""])
        if report.agent_assisted_attempts:
            for attempt in report.agent_assisted_attempts:
                lines.extend(
                    [
                        (
                            f"- `{attempt.attempt_id}`: {attempt.description} "
                            f"(workflow succeeded: {attempt.workflow_succeeded})"
                        )
                    ]
                )
        else:
            lines.append("- No agent-assisted attempt was supplied.")
        lines.extend(["", "## Failures", ""])
        if report.failures:
            lines.extend(
                f"- `{failure.failure_id}`: {failure.detail}"
                for failure in report.failures
            )
        else:
            lines.append("- No failure record was supplied.")
        lines.extend(["", "## Diagnosis", ""])
        if report.diagnoses:
            lines.extend(
                f"- {diagnosis.status.value}: {diagnosis.policy_rejection or diagnosis.stop_reason or 'diagnosis recorded'}"
                for diagnosis in report.diagnoses
            )
        else:
            lines.append("- No diagnosis record was supplied.")
        lines.extend(["", "## Repairs", ""])
        if report.repairs:
            lines.extend(
                f"- `{repair.action.action_id}`: {repair.status.value}"
                for repair in report.repairs
            )
        else:
            lines.append("- No repair record was supplied.")
        lines.extend(["", "## Verification", ""])
        if report.verification is not None:
            lines.append(
                f"- Status: `{report.verification.status.value}` at `{report.verification.level.value}`"
            )
            lines.append(f"- {report.verification.summary}")
        else:
            lines.append("- Verification was not supplied.")
        lines.extend(["", "## Goal coverage", ""])
        if report.goal_coverage is not None:
            lines.append(f"- Status: `{report.goal_coverage.coverage_status}`")
            lines.append(
                "- Required milestones: "
                + ", ".join(report.goal_coverage.required_milestones)
            )
            lines.extend(
                f"- `{item.milestone}`: `{item.status.value}` — {item.detail}"
                for item in report.goal_coverage.observations
            )
        else:
            lines.append("- No explicit milestone coverage contract was supplied.")
        lines.extend(["", "## Final status", ""])
        if report.final_status is not None:
            lines.append(f"- `{report.final_status.status.value}`")
            lines.extend(f"- {reason.detail}" for reason in report.final_status.reasons)
        else:
            lines.append("- Final status was not supplied.")
        lines.extend(["", "## Blockers", ""])
        if report.blockers:
            lines.extend(f"- {blocker}" for blocker in report.blockers)
        else:
            lines.append("- No blocker record was supplied.")
        lines.extend(["", "## Documentation gaps", ""])
        if report.documentation_gaps:
            lines.extend(f"- {gap}" for gap in report.documentation_gaps)
        else:
            lines.append("- No documentation-gap record was supplied.")
        return self._safe_text("\n".join(lines) + "\n")

    @staticmethod
    def _safe_json(value: object) -> str:
        serialized = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        safe = redact_sensitive_text(serialized)
        try:
            decoded = json.loads(safe)
            if isinstance(decoded, dict):
                reject_sensitive_mapping(decoded)
        except json.JSONDecodeError as exc:
            raise ReportError("Redaction produced invalid JSON.") from exc
        return safe

    @staticmethod
    def _safe_text(value: str) -> str:
        if redact_sensitive_text(value) != value:
            raise ReportError("Report artifacts contain credential-like material.")
        return value

    def _write_text(self, relative: str, value: str) -> None:
        target = (self.root / relative).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise ReportError(
                "Report artifact path escapes the run directory."
            ) from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding="utf-8", newline="\n")
