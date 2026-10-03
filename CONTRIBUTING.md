# Contributing to ReproScout

Thank you for helping improve reproducibility tooling. Please read
[`AGENTS.md`](AGENTS.md), [`docs/PROJECT_SPEC.md`](docs/PROJECT_SPEC.md), and
[`docs/ROADMAP.md`](docs/ROADMAP.md) before making a substantial change.

## Development setup

```bash
git clone https://github.com/emam26/ReproScout.git
cd ReproScout
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Run the ordinary checks before opening a pull request:

```bash
pytest
ruff check .
ruff format --check .
cd frontend && npm ci && npm run lint && npm test && npm run build
```

Docker integration tests are explicit and must run against only the local
ReproScout-owned containers:

```bash
pytest -m docker
docker ps -a --filter label=reproscout.managed=true
```

Do not use Docker prune commands or remove unrelated containers. Do not run
live provider calls in ordinary tests; use `MockLLMProvider` and controlled
fixtures. Keep changes focused on one roadmap phase, preserve evidence and
security boundaries, and add deterministic tests for meaningful behavior.

## Pull requests

Explain the research/engineering question, the smallest implementation, tests
run, Docker or network requirements, and any remaining limitations. Never add
credentials, generated runs, model weights, datasets, or private artifacts.
