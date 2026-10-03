# Local API

The optional API is a local development/control interface over the same
application service used by the CLI. Install it with:

```bash
pip install -e ".[api]"
reproscout serve
```

The server binds to `127.0.0.1:8000` by default. It is not a hardened
multi-tenant public execution service. Do not expose it to the public Internet
or place secrets in request bodies. No endpoint accepts arbitrary shell
commands, arbitrary host paths, API keys, authorization headers, or private
environment values.

Versioned endpoints:

```text
GET  /api/v1/health
GET  /api/v1/version
GET  /api/v1/config
POST /api/v1/audits
GET  /api/v1/runs
GET  /api/v1/runs/{run_id}
GET  /api/v1/runs/{run_id}/events
GET  /api/v1/runs/{run_id}/report
POST /api/v1/runs/{run_id}/reproduce
```

`POST /api/v1/audits` accepts the validated repository URL, goal, optional
provider/model selection, and `no_ai`. It runs the bounded workflow
synchronously using the existing SQLite state and Docker-only execution
boundary. The endpoint is intentionally simple for local use; it is not a
distributed job queue or production worker system.

`GET /api/v1/config` returns secret-free local capability status for the
dashboard: Python and platform facts, Git availability, Docker availability,
the configured runs directory and writeability, and the selected LLM provider
with only a boolean configured flag. It never returns credential values.

`POST /api/v1/runs/{run_id}/reproduce` is backed by the persisted `plan.json`
and source workspace created by an audit. It replays that plan in a newly copied
workspace and newly created Docker sandbox. It fails rather than claiming
success when the required artifacts are absent.

Generated OpenAPI documentation is available at `/docs` while the local server
is running.
