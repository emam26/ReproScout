# Public CLI

ReproScout exposes a local, bounded command-line workflow. The main command is:

```text
reproscout audit <github-url> [--goal auto|install|tests|demo] [--runs-dir PATH] [--no-ai]
```

`audit` calls the shared application service for intake, deterministic analysis,
finite planning, Docker-only execution, objective verification, clean-room
verification when the first workflow succeeds, status calculation, and report
writing. It prints a concise summary; `--json` emits a machine-readable result.

The other supported commands are:

```text
reproscout inspect <github-url> [--runs-dir PATH] [--no-ai] [--json]
reproscout doctor [--json]
reproscout runs [--runs-dir PATH] [--json]
reproscout status <run-id> [--runs-dir PATH] [--json]
reproscout report <run-id> [--runs-dir PATH]
reproscout cleanup [--run-id RUN_ID] [--json]
reproscout config [--json]
reproscout run <github-url> [--goal ...] [--runs-dir PATH] [--json]
reproscout version
```

`inspect` clones and analyzes without executing target code. The compatibility
`run` command performs intake and manifest generation only. `doctor` reports
secret-free local capability checks. `cleanup` lists and removes only containers
with both the ReproScout ownership label and, when supplied, the requested run
label. ReproScout intentionally does not expose a shell command, arbitrary host
path execution, or a `reproduce` command until a persisted recipe can be safely
executed by the existing control plane.

Successful operations return zero. Invalid options return Typer's usage error;
intake, audit, and artifact failures return one with a bounded message. Run IDs
are validated before filesystem access. Provider keys are read only from the
environment and are never included in CLI output, reports, events, or JSON.
