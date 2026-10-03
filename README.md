# 🧭 ReproScout

> Autonomous agent for reproducing, diagnosing, and repairing open-source research projects.

[![CI](https://github.com/emam26/ReproScout/actions/workflows/ci.yml/badge.svg)](https://github.com/emam26/ReproScout/actions/workflows/ci.yml)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

ReproScout helps answer a practical question: **can someone reproduce this
research repository from a clean environment, and what evidence explains the
answer?** Give it a public GitHub repository. It inspects the project, follows
its documented workflow, runs target commands in Docker, records failures,
diagnoses likely causes, and produces an evidence-backed report.

> [!NOTE]
> ReproScout is pre-release. The package and CLI now use the `reproscout` name;
> PyPI publication and hosted deployment are still future work.

## What is ReproScout?

ReproScout is a reproducibility auditor for public Python research and AI/ML
projects. It is more than a static file checker: it can inspect a repository,
build a bounded plan, execute that plan in a Docker sandbox, collect evidence,
and let deterministic verification decide the result.

```text
Repository → Inspect → Plan → Docker execution → Diagnose → Verify → Report
```

The core also contains policy-checked repair experiments and clean-room replay
support. The public `audit` command does not silently modify a target repository
or claim an automatic source repair.

## Why ReproScout?

Research repositories often depend on undocumented Python versions, stale
packages, missing assets, incorrect paths, or machine-specific assumptions.
Finding the real blocker manually can take hours. ReproScout automates the
audit while preserving the commands, outputs, failures, environment facts, and
verification evidence needed to review what happened.

## How it works

1. **Intake** — validate a public GitHub URL, clone it with sterile Git settings,
   and record the exact commit.
2. **Analyze** — inspect README instructions, dependency files, tests, Docker,
   and other bounded project context.
3. **Plan** — create a finite workflow from documented and detected commands.
4. **Execute** — install and run target commands only inside a bounded Docker
   sandbox.
5. **Diagnose** — normalize failures and build a bounded evidence bundle;
   optional LLM reasoning can explain ambiguous failures.
6. **Repair and retry** — use typed, policy-checked repair capabilities when a
   controlled repair experiment is explicitly run.
7. **Verify** — check exit codes, tests, artifacts, and configured expectations
   with deterministic code.
8. **Clean-room rerun** — rerun successful workflows in a fresh workspace when
   the verification contract requires it.
9. **Report** — write machine-readable state and a human-readable summary.

## Where AI is used

Deterministic tools handle repository inspection, dependency parsing, command
execution, failure capture, state transitions, and verification. An LLM is
optional and is used only for interpretation: understanding ambiguous setup
instructions, forming failure hypotheses, or selecting among permitted repair
options.

> [!IMPORTANT]
> The LLM never decides that reproduction succeeded. The verifier decides from
> recorded execution and artifact evidence.

## Quick start

ReproScout is not published to PyPI yet. Install the current development
checkout instead. Python 3.11+ and Docker are required for target execution.

```bash
git clone https://github.com/emam26/ReproScout.git
cd ReproScout
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
python -m pip install -e .
```

Check prerequisites and inspect a repository without executing its code:

```bash
reproscout doctor
reproscout inspect https://github.com/user/project --no-ai
```

Run a bounded audit:

```bash
reproscout audit https://github.com/user/project --goal auto --no-ai
```

Use `--runs-dir ./local-runs` when you want artifacts stored in a specific
directory. The command prints the run ID, status, verification level, attempts,
report path, and clean-room package path when available.

## What happens when a repository fails?

ReproScout does not hide a failed command behind an LLM explanation. It keeps
the deterministic failure class and bounded evidence, then records any optional
diagnosis separately. A diagnosis is a hypothesis, not proof, and a proposed
repair cannot bypass the repair policy or verifier.

An illustrative report might distinguish:

```text
Official documented reproduction: FAILED
Agent-assisted reproduction: PARTIAL

Evidence: dependency installation failed under the documented environment
Diagnosis: an undocumented Python-version constraint is likely
Verification: reproduction not claimed until required checks pass
```

The result is one of:

| Status | Meaning |
| --- | --- |
| `REPRODUCED` | Required objective checks and any required clean-room rerun passed. |
| `PARTIAL` | The workflow is not fully confirmed, or required verification is incomplete. |
| `BLOCKED` | Required evidence or a safe verification path is unavailable. |
| `FAILED` | Execution or an objective verification condition failed. |
| `UNSAFE` | A deterministic safety check rejected the workflow. |

## CLI

The current CLI is deliberately small:

| Command | Purpose |
| --- | --- |
| `doctor` | Check local prerequisites without revealing credentials. |
| `inspect <url>` | Clone and analyze a repository without executing target code. |
| `audit <url>` | Run intake, analysis, Docker execution, verification, and reporting. |
| `runs` | List persisted runs. |
| `status <run-id>` | Show persisted control-plane state. |
| `report <run-id>` | Display a run's Markdown report. |
| `cleanup` | Remove only explicitly owned Docker containers. |
| `version` | Print the installed version. |
| `serve` | Start the optional local API. |

See [`docs/CLI.md`](docs/CLI.md) for options and machine-readable output.

## Output and reports

Each audit stores its evidence under the configured runs directory. Depending
on the workflow, a run can contain:

```text
report.md          human-readable result
run.json           schema-versioned report data
events.jsonl       state and tool events
commands.jsonl     executed command records
environment.json   captured environment facts
patches.diff       repair changes, when applicable
reproduce.sh       a bounded reproduction recipe, when applicable
```

Reports distinguish the documented workflow from any agent-assisted evidence.
Review artifacts before sharing them: they may contain project-specific paths,
outputs, or failure details.

## Current scope

The first release focuses on:

- public HTTPS GitHub repositories;
- Python projects, including common `requirements.txt`, `pyproject.toml`,
  `setup.py`, `setup.cfg`, and environment hints;
- documented installs, tests, lightweight demos, and primarily CPU workflows;
- Docker-backed execution with bounded time, output, resources, and network;
- pytest-oriented verification and evidence-backed reporting.

It is not intended to reproduce multi-day training runs, multi-GPU systems,
giant datasets, full paper metrics, private or license-restricted assets, or
arbitrary operating systems.

## Safety

Target repository code is untrusted. ReproScout uses Docker as a defense-in-
depth boundary and does not fall back to executing target code on the host.
Sandboxes are non-privileged, drop capabilities, use `no-new-privileges`, and
apply workspace, timeout, output, and resource limits. The Docker socket and
host credentials are not mounted, and network access is denied unless the
bounded plan explicitly requires it.

Cleanup matches only containers explicitly owned by this tool; it never
performs global Docker cleanup.

> [!WARNING]
> Docker is not a perfect hostile-code or malware boundary. Do not expose the
> local API to the public Internet, mount the Docker socket, or use ReproScout
> with highly sensitive host data.

See [`SECURITY.md`](SECURITY.md) for the security policy and limitations.

## LLM providers

LLM use is optional. The default path is deterministic and can run with
`--no-ai`; offline tests use the mock provider. Gemini and Groq adapters exist,
but this pre-release README does not claim live validation for either provider.

If you explicitly enable a provider, configure credentials through environment
variables only:

```bash
LLM_PROVIDER=gemini
LLM_MODEL=<provider-model>
GEMINI_API_KEY=<your-key>
```

Live Gemini checks are available only through the explicit opt-in path in
[`docs/LLM_PROVIDERS.md`](docs/LLM_PROVIDERS.md); the normal test suite remains
offline.

Never commit or paste credentials into repositories, reports, prompts, or issue
threads. Provider quota and rate limits are controlled by the provider.

> [!TIP]
> Use `--no-ai` or the mock provider while developing so ordinary tests remain
> offline and do not consume provider quota.

## Local API and dashboard

The optional FastAPI control API uses the same application service as the CLI
and binds to loopback by default:

```bash
python -m pip install -e ".[api]"
reproscout serve
```

The API exposes versioned health, audit, run, event, report, and persisted-plan
replay endpoints. It is a local development/control API, not a hardened
multi-tenant service. See [`docs/API.md`](docs/API.md).

The React/TypeScript dashboard is source-checkout tooling:

```bash
cd frontend
npm install
npm run dev
```

See [`docs/DASHBOARD.md`](docs/DASHBOARD.md) for the local workflow.

## Architecture

```text
CLI / API / Dashboard
          ↓
      ReproScout core
          ↓
Intake → Analysis → Plan → Docker execution
                              ↓
                    Diagnosis / repair policy
                              ↓
                         Verification
                              ↓
                    Clean-room rerun → Report
```

The governing separation is: **LLM = reasoning, tools = execution, state =
control, verifier = truth, Docker = isolation.** See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the detailed design.

## Development

```bash
python -m pip install -e ".[dev]"
pytest
ruff check .
ruff format --check .
```

Docker integration tests are explicit:

```bash
pytest -m docker
```

Frontend checks run from `frontend/`:

```bash
npm run lint
npm test
npm run build
```

Contributions should also follow [`CONTRIBUTING.md`](CONTRIBUTING.md). ReproScout
is licensed under the [MIT License](LICENSE). See [`CHANGELOG.md`](CHANGELOG.md)
for the current release history.
