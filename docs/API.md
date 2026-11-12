# Local API

The optional API is a local development/control interface over the same
application service used by the CLI. Install it with:

```bash
python -m pip install -e ".[api]"
reproscout serve
```

For an existing editable `uv tool` installation, reinstall with the optional
extra rather than assuming it was included:

```bash
uv tool install --reinstall --editable ".[api]"
reproscout serve
```

The server binds only to `127.0.0.1` or `::1` (`127.0.0.1:8000` by default).
It is not a hardened multi-tenant public execution service. Do not expose it
to the public Internet or place secrets in request bodies. No endpoint accepts arbitrary shell
commands, arbitrary host paths, API keys, authorization headers, or private
environment values.

`GET /api/v1/health` answers only whether this local FastAPI process is
available. It does not run Docker checks and does not establish sandbox
readiness. Use `GET /api/v1/config` for the current, secret-free Docker
capability facts; a preflight or explicit `reproscout setup` probe records
actual sandbox execution evidence.

Versioned endpoints:

```text
GET  /api/v1/health
GET  /api/v1/version
GET  /api/v1/config
POST /api/v1/audits
POST /api/v1/preflights
POST /api/v1/preflight
GET  /api/v1/runs
GET  /api/v1/runs/{run_id}
GET  /api/v1/runs/{run_id}/events
GET  /api/v1/runs/{run_id}/report
GET  /api/v1/runs/{run_id}/environment-report
GET  /api/v1/runs/{run_id}/environment-evidence
GET  /api/v1/runs/{run_id}/agent-handoff
POST /api/v1/runs/{run_id}/reproduce
```

`POST /api/v1/audits` accepts the validated repository URL, goal, optional
provider/model selection, and `no_ai`. It runs the bounded workflow
synchronously using the existing SQLite state and Docker-only execution
boundary. The endpoint is intentionally simple for local use; it is not a
distributed job queue or production worker system.

`POST /api/v1/preflights` (with `/preflight` retained as a singular alias)
accepts the validated public repository URL and runs the existing safe-by-
default environment-preflight service. It does not execute the target's tests,
demos, training, evaluations, or experiments. The three fixed artifact routes
return bounded, redacted content from that run; they do not accept arbitrary
filenames or host paths.

`GET /api/v1/config` returns secret-free local capability status for the
dashboard: Python and platform facts, Git availability, Docker daemon/context/
seccomp capability facts, the configured runs directory and writeability, and
the selected LLM provider with only a boolean configured flag. It never returns
credential values or Docker command/context endpoint details.

`POST /api/v1/runs/{run_id}/reproduce` is backed by the persisted `plan.json`
and source workspace created by an audit. It replays that plan in a newly copied
workspace and newly created Docker sandbox. It fails rather than claiming
success when the required artifacts are absent.

Generated OpenAPI documentation is available at `/docs` while the local server
is running.
