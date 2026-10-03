# Controlled Workspace Repairs

Phase 10 adds the bounded file-editing boundary used by repair experiments.
`WorkspaceEditor` receives one existing target workspace and never accepts an
arbitrary host root. It supports UTF-8 reads, exact single-occurrence
replacement, standard unified patches, exact diffs, and rollback.

## Safety and audit guarantees

Every path is normalized and resolved beneath the workspace. Absolute paths,
`..` traversal, `.git` metadata, symlink paths, and ReproScout's own project
root are rejected. File size, patch size, changed-file count, and changed-byte
limits are enforced before writes. Writes use a temporary file in the target
file's parent followed by an atomic replacement.

Each change records the original and resulting SHA-256, byte size, relative
path, and unified diff. A repair run writes the exact combined diff to
`patches.diff` under that run's artifact directory. Rollback first verifies
that every edited file still has the expected post-edit bytes; an external
change causes a typed conflict instead of silently overwriting it.

The editor rejects credential-shaped material in proposed replacements and
patches. It does not invoke a shell, Git, a package manager, or target code.

## Retry boundary

The execution engine can leave a failed attempt in `DEBUG`. The controlled
pipeline then accepts a typed diagnosis callback, applies one minimal patch,
executes one Docker-backed retry as an objective observation, records the
result, and rolls the workspace back. A changed failure signature or verified
workflow success is recorded as improvement; no final reproducibility status is
created by this experiment.

The retry can remain in `DEBUG` even when it succeeds. Formal verification and
clean-room reproduction must still occur before a run can claim
`REPRODUCED`. Working, repairable, unrepairable, and no-claim fixture tests
cover these boundaries without executing target code on the host.
