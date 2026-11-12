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
* bridge networking by default, or no networking when configured
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
repository-controlled venv is never selected or reused. The only managed-directory
validation exception is the exact Python venv `lib64 -> lib` link under
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

## Known limitations

Docker containers reduce risk but are not a perfect security boundary. Network
policy is currently limited to Docker bridge or no networking, and stronger
resource, syscall, filesystem, and image provenance policies belong to later
safety work. Phase 2 does not infer environments, install dependencies, run
the Phase 1 CLI workflow, or provide an LLM.
