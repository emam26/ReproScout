# Docker Sandbox

Phase 2 provides the execution boundary that later phases can use for target
repository commands. It does not change the CLI reproduction workflow yet.

## Design

`Sandbox` defines the lifecycle and execution interface. `DockerSandbox`
implements it with the Docker CLI:

```text
SandboxConfig
      ↓
DockerSandbox.create()
      ↓
labeled disposable container
      ↓
DockerSandbox.execute(command)
      ↓
ExecutionResult
      ↓
DockerSandbox.destroy()
```

The default image is the official `python:3.11-slim` image. A workspace is
mounted read/write at `/workspace`, and commands run with that as their working
directory. The implementation supports native Docker CLI discovery and the WSL
Docker CLI on Windows.

## Defaults and limits

Each container receives:

* `--cap-drop=ALL`
* `--security-opt=no-new-privileges`
* no networking by default; a plan may select bridge networking only when a
  permitted dependency-installation step requires it
* 512 MB memory
* 1 CPU
* 128 PID limit
* a 30-second command timeout
* a 120-second container-creation timeout
* a read-only container filesystem with temporary `/tmp` and `/runtime` tmpfs

The requested workspace remains writable for future controlled repair phases.
Only that workspace is mounted. `.env`, SSH-key filenames, common cloud
credential directories, and repository-controlled symlinks are rejected from
mounted workspaces. The sandbox-managed Python environment is created in a
container-private, UID/GID-owned `/runtime` tmpfs; a pre-existing
repository-controlled venv is never selected or reused. The runtime tmpfs is
explicitly executable for its managed interpreter but remains `nosuid`, `nodev`,
and mode `700`. The only managed-directory validation exception is the exact Python
venv `lib64 -> lib` link under
`.reproscout/venv`, and it is accepted only when the destination remains inside
that venv and the expected venv files are real files.

## Ownership and cleanup

Containers are labeled with:

```text
reproscout.managed=true
reproscout.run_id=<run-id>
```

The exact container ID is retained by the `DockerSandbox` instance. Cleanup
removes only that ID (or the unique ReproScout-generated name if creation
fails). Timeout handling destroys the owned container before returning a
timed-out result. ReproScout never performs global Docker cleanup.

## Evaluation preflight profile

`reproscout preflight <github-url> --profile evaluation` keeps all existing
sandbox controls and selects a fixed lower execution policy: at most five
setup-only steps, 180 seconds per step, and a 900-second plan budget. It does
not raise the Docker CPU, memory, PID, networking, capability, filesystem, or
ownership settings.

The execution engine gives sandbox creation the remaining plan budget and
rechecks the deadline before each command. Docker daemon availability checks,
management calls, and forced cleanup are separately bounded, so the profile
does not claim a strict end-to-end 900-second wall-clock guarantee. It also
does not meter network download volume or cumulative storage for host clones,
run directories, Docker images, or daemon cache. Enforce those evaluator-wide
limits with external filesystem quotas and a metered or allowlisted egress
proxy; the generated evidence marks them as external and unverified.

## Testing

Offline tests cover profile selection, immutable resource settings, artifact
disclosure, deadline exhaustion before command launch, and cleanup-failure
classification. Docker integration tests remain a separate, daemon-dependent
validation gate and must verify exact owned-container cleanup.

## Known limitations

Docker containers reduce risk but are not a perfect security boundary. Network
policy is currently limited to Docker bridge or no networking, and stronger
resource, syscall, filesystem, and image provenance policies belong to later
safety work. Phase 2 does not infer environments, install dependencies, run
the Phase 1 CLI workflow, or provide an LLM.
