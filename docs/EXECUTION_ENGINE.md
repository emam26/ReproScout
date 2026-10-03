# Initial Execution Engine

Phase 7 executes a finite `ReproductionPlan` and records raw execution evidence.
It does not diagnose failures, edit the target repository, retry with repairs, or
issue a formal reproducibility verdict.

## Architecture

`PlanExecutionEngine` accepts an existing Phase 3 run at `PLAN`, a validated
Phase 6 plan, a run directory, and a target workspace. Its default backend is
`DockerSandbox`; there is no subprocess or host-execution fallback. Tests can
inject a `Sandbox` implementation without changing production behavior.

The engine moves application-controlled state through `SETUP`, `EXECUTE`,
`VERIFY`, `REPORT`, and `DONE`. Each attempted command creates the existing
Phase 3 `Attempt`, `ToolCall`, and `ToolResult` records. `RunOutcome.SUCCEEDED`
means that the workflow completed without an execution failure. It does not mean
`REPRODUCED` and is not a verification status.

## Deterministic execution policy

Plan steps execute sequentially in one disposable, labeled Docker container.
The first unavailable prerequisite, non-zero exit, timeout, sandbox failure, or
resource/network/dependency failure stops the plan. Phase 7 performs no repair,
source change, speculative retry, or LLM-controlled state transition.

Working directories are workspace-relative and translated to `/workspace` in
the container. The plan safety layer rejects credential material, privileged
operations, Docker socket access, container/global cleanup, host system paths,
and destructive commands before execution. Target-project dependencies are
therefore installed only by plan commands inside Docker. Container networking is
disabled unless at least one validated plan step explicitly requires it.

The Docker sandbox applies its existing memory, CPU, PID, capability,
`no-new-privileges`, read-only-root, and timeout controls. Cleanup addresses only
the exact container created for the run and retains the ownership labels:

```text
reproscout.managed=true
reproscout.run_id=<run-id>
```

## Failure classes

The execution result distinguishes `COMMAND_NONZERO`, `TIMEOUT`,
`SANDBOX_FAILURE`, `DEPENDENCY_INSTALLATION_FAILURE`, `NETWORK_FAILURE`,
`RESOURCE_LIMIT_FAILURE`, and `PREREQUISITE_UNAVAILABLE`. Classification uses
exit codes, timeout state, plan action type, and bounded command output. It does
not claim that a repository is permanently unreproducible.

## Evidence and artifacts

The engine writes only below the supplied run directory:

```text
runs/<run-id>/
├── commands.jsonl
├── events.jsonl
├── logs/
│   ├── setup.log
│   └── execution.log
└── workspace/
```

`commands.jsonl` contains structured step results, including step and attempt
IDs, timestamps, duration, working directory, exit code, timeout state, bounded
stdout/stderr, failure class, and container ID. `events.jsonl` is an ordered
snapshot of the authoritative append-only event log. Output is redacted before
persistence and is separately bounded for state and text logs. Credentials,
authorization headers, bearer tokens, passwords, and access tokens must not be
stored in state or artifacts.

## Tests and current boundary

Offline unit tests use an injected fake sandbox to verify ordering, failure
stopping, persistence, failure classification, working directories, output
bounds, and secret redaction. Docker integration fixtures exercise the complete
manifest → analysis → plan → execution path for success, failure, and timeout,
including exact container cleanup.

The existing CLI remains intake-only. Wiring `reproscout run` through the full
pipeline is deferred until its configuration and lifecycle interface are stable.
Autonomous diagnosis/repair and controlled source editing begin no earlier than
Phase 8.
