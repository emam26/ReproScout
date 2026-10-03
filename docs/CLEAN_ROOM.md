# Clean-room Reproduction

Phase 14 derives a `CleanRoomRecipe` from a real reproduction plan and optional
real repair diff. `CleanRoomRunner` creates a new run ID, performs a fresh clone
from the recorded repository URL, verifies the pinned commit SHA, creates a new
workspace and Docker sandbox, reapplies only the supplied diff, writes a
portable command recipe, and executes the plan through the existing Docker
engine.

The already-executed workspace is not copied into the clean run and the prior
sandbox is not reused. The clean run receives a fresh state history and its own
artifacts. This avoids inheriting virtual environments, caches, generated files,
or first-run outputs.
The runner verifies the new execution and computes final status with
`clean_room_required=True`; a successful repaired attempt alone cannot satisfy
that condition.

When evidence permits, the clean run contains `reproduce.sh`,
`REPRODUCTION.md`, and `patches.diff`. It does not fabricate an environment
lock or Dockerfile when the exact image/environment evidence was not supplied.
The Docker integration fixture supplies an isolated clone callback for its local
test repository; production uses `clone_repository_at_commit`. The success
fixture proves fresh-source repair and reproduction; the failure fixture proves
a clean-room failure is not claimed as `REPRODUCED`.
