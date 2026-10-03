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
* a read-only container filesystem with a temporary `/tmp`

The requested workspace remains writable for future controlled repair phases.
Only that workspace is mounted. `.env`, SSH-key filenames, common cloud
credential directories, and symlinks are rejected from mounted workspaces.

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
