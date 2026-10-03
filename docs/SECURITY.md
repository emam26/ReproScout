# Untrusted Repository Security Boundary

Phase 15 hardens the existing Docker-only execution and intake paths. The
controls are intentionally layered; this is a containment boundary for the V1
auditor, not a claim that arbitrary hostile code is harmless.

## Sandbox controls

Target execution defaults to `--network=none`. A plan step must explicitly
require networking before the execution engine requests Docker bridge mode.
Containers use read-only roots, a writable target workspace only, a private
temporary filesystem, dropped capabilities, `no-new-privileges`, memory/CPU/PID
limits, command and creation timeouts, and bounded workspace file count and
bytes. Docker socket mounts, privileged mode, host networking, and global
container-management commands remain prohibited. Cleanup removes only the
owned container ID.

Execution output and diagnostic evidence remain bounded and redacted. The
workspace validator rejects symlinks, credential files/directories, and
oversized workspaces before mounting. The Phase 10 editor adds path, hash,
patch, and rollback controls inside that workspace.

## URL and asset controls

`reproscout.network` accepts only credential-free HTTPS URLs to public
destinations. It rejects loopback, link-local, private, reserved, multicast,
unspecified, localhost, `.local`, and internal hostnames. Downloads enforce
timeouts, byte limits, and redirect counts. Redirect targets are parsed and
resolved through the same policy before a new request; response bodies are
streamed only up to the configured bound. Phase 9 additionally requires an
asset URL to be present in trusted evidence and rejects query-string
credentials.

## Git intake controls

Git runs without a shell, with terminal prompts disabled, system/global config
disabled, credential helpers neutralized, optional locks disabled, and Git LFS
smudge disabled. Clone does not recurse into submodules and uses an inert hooks
path. Submodule, LFS, and DVC indicators remain evidence for later policy
decisions rather than automatic execution.

The security tests cover private/redirecting URLs, bounded downloads, sterile
Git configuration, default network denial, workspace limits, credential-file
rejection, Docker socket absence, and safe PID-limit behavior. Tests do not
run destructive host attacks or perform global Docker cleanup.
