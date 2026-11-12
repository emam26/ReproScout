# Local dashboard

The dashboard is a small Vite/React/TypeScript application in `frontend/`. It
is intended for local use with the API. The public site is a separate static
build mode from the same frontend and never connects to the local API.

Install and validate it with:

```bash
cd frontend
npm ci
npm run typecheck
npm test
npm run build
npm run dev
```

Vite serves the dashboard on `127.0.0.1:5173` and proxies `/api` to the local
ReproScout API on `127.0.0.1:8000`. The production preview uses the same local
proxy configuration. Start the API separately with `reproscout serve`.

The dashboard supports:

* starting an environment preflight with a repository URL and `--no-ai` equivalent,
* retaining the legacy audit workflow as an explicit opt-in,
* prior-run browsing and bounded five-second polling for active runs,
* run workflow/stage/outcome, readiness, timeline, failure/diagnosis/repair
  counts, blockers, and report information,
* secret-free settings/config status for Docker, the LLM mode, Git, and the
  configured runs directory,
* loading the bounded environment report, structured evidence, agent handoff, or
  legacy report as escaped text.

Repository content, logs, and report text are rendered through React text nodes;
the dashboard does not use `dangerouslySetInnerHTML`. API keys are never sent
to or bundled into frontend JavaScript.

## Public site

The same Vite project can produce a static, backend-free public site for
GitHub Pages:

```bash
cd frontend
npm run build:public
```

The public build uses `/ReproScout/` as its base path and contains no local API
submission control. It is deployed only by `.github/workflows/pages.yml` after
validation on `main`. The repository owner must enable GitHub Pages with
**Settings -> Pages -> Source: GitHub Actions**, then verify the published URL
and confirm that the local dashboard remains connected only to a local API.
