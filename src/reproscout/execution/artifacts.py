"""Bounded JSONL and text artifacts for one execution run."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from reproscout.state import Event
from reproscout.state.models import format_timestamp

from .models import RunArtifactPaths, StepExecutionResult


@dataclass(slots=True)
class RunArtifacts:
    """Own only files beneath one explicitly supplied run directory."""

    run_directory: Path
    workspace: Path

    @classmethod
    def create(cls, run_directory: Path, workspace: Path) -> RunArtifacts:
        run_root = Path(run_directory).resolve()
        workspace_path = Path(workspace).resolve()
        run_root.mkdir(parents=True, exist_ok=True)
        (run_root / "logs").mkdir(parents=True, exist_ok=True)
        if not workspace_path.is_dir():
            raise ValueError(f"Execution workspace does not exist: {workspace_path}")
        return cls(run_root, workspace_path)

    @property
    def paths(self) -> RunArtifactPaths:
        return RunArtifactPaths(
            run_directory=str(self.run_directory),
            commands_jsonl=str(self.run_directory / "commands.jsonl"),
            events_jsonl=str(self.run_directory / "events.jsonl"),
            setup_log=str(self.run_directory / "logs" / "setup.log"),
            execution_log=str(self.run_directory / "logs" / "execution.log"),
            patches_diff=str(self.run_directory / "patches.diff"),
            workspace=str(self.workspace),
        )

    def append_step(
        self,
        result: StepExecutionResult,
        *,
        setup: bool,
        log_stdout: str | None = None,
        log_stderr: str | None = None,
    ) -> None:
        command_path = self.run_directory / "commands.jsonl"
        with command_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(
                json.dumps(
                    result.model_dump(mode="json"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
            )
        log_name = "setup.log" if setup else "execution.log"
        with (self.run_directory / "logs" / log_name).open(
            "a",
            encoding="utf-8",
            newline="\n",
        ) as stream:
            stream.write(f"[{result.step_id}] {result.command or '(no command)'}\n")
            stdout = result.stdout if log_stdout is None else log_stdout
            stderr = result.stderr if log_stderr is None else log_stderr
            if stdout:
                stream.write(f"stdout:\n{stdout}\n")
            if stderr:
                stream.write(f"stderr:\n{stderr}\n")
            stream.write(
                f"exit_code={result.exit_code} timed_out={result.timed_out} "
                f"failure={result.failure_kind or 'NONE'}\n\n"
            )

    def write_events(self, events: list[Event]) -> None:
        path = self.run_directory / "events.jsonl"
        with path.open("w", encoding="utf-8", newline="\n") as stream:
            for event in events:
                stream.write(
                    json.dumps(
                        {
                            "event_type": event.event_type.value,
                            "payload": event.payload,
                            "run_id": event.run_id,
                            "sequence": event.sequence,
                            "stage": event.stage.value,
                            "timestamp": format_timestamp(event.timestamp),
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                    + "\n"
                )
