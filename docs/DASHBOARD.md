# Local dashboard

The dashboard is a small Vite/React/TypeScript application in `frontend/`. It
is intended for local use with the Phase 21 API and is not deployed by this
repository.

Install and validate it with:

```bash
cd frontend
npm install
npm run typecheck
npm test
npm run build
npm run dev
```

Vite serves the dashboard on `127.0.0.1:5173` and proxies `/api` to the local
ReproScout API on `127.0.0.1:8000`. The production preview uses the same local
proxy configuration. Start the API separately with `reproscout serve`.

The dashboard supports:

* starting an audit with a repository URL, goal, and `--no-ai` equivalent,
* prior-run browsing and bounded five-second polling for active runs,
* run stage/outcome, verification, timeline, failure/diagnosis/repair counts,
  blockers, and report information,
* secret-free settings/config status for Docker, the LLM mode, Git, and the
  configured runs directory,
* loading the bounded human-readable report as escaped text.

Repository content, logs, and report text are rendered through React text nodes;
the dashboard does not use `dangerouslySetInnerHTML`. API keys are never sent
to or bundled into frontend JavaScript.
