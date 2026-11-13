

<p align="center">
  <img
    src="./frontend/public/reproscout-logo.png"
    alt="ReproScout compass logo"
    width="115"
  />
</p>


<h1 align="center">ReproScout</h1>

<p align="center">
  <strong>Verify research environments before reproducing results.</strong>
</p>

<p align="center">
  <a href="#overview"><img src="https://img.shields.io/badge/ReproScout-0.1.0-0F766E" alt="Version"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white" alt="Python"></a>
  <a href="https://react.dev/"><img src="https://img.shields.io/badge/React-local%20dashboard-61DAFB?logo=react&logoColor=black" alt="React"></a>
  <a href="https://typer.tiangolo.com/"><img src="https://img.shields.io/badge/Typer-Python%20CLI-3776AB" alt="Typer"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green.svg" alt="MIT License"></a>
</p>


## Overview

ReproScout is an open-source Python tool that helps researchers and coding agents prepare to reproduce research projects. It inspects repository requirements, analyzes dependencies, and verifies environment setup in a bounded Docker sandbox. Before any scientific experiment begins, it generates evidence-backed reports showing what worked, what failed, and what needs attention.

By default, ReproScout focuses on environment readiness rather than executing experiments. Running tests or project-specific workflows requires an explicit, bounded `audit`. **A ready environment does not mean the research results have been reproduced.**

## Features

| Capability | What it provides |
| --- | --- |
| Environment preflight | Detects Python requirements, declared dependencies, system-tool needs, setup instructions, and missing prerequisites. |
| Evidence-backed outcomes | Distinguishes verified readiness, partial setup, failed installation, missing prerequisites, unsafe conditions, and unverified capabilities. |
| Isolated execution | Uses controlled Docker execution for permitted setup and lightweight verification work when Docker is available and suitable. |
| Optional reasoning | Can use configured LLM providers to interpret setup instructions or diagnose errors; LLM output never establishes success. |
| Research-agent handoff | Writes a concise report, machine-readable evidence, and an agent-ready summary with the pinned revision and unresolved issues. |
| Local interfaces | Includes a Typer CLI, an optional loopback-only FastAPI service, and a local React dashboard. |

## Installation

Install ReproScout from a source checkout; it is not presented as a PyPI package. Python 3.11 or later is required. Docker is a separate runtime prerequisite for sandboxed work; installation of the CLI does not install or validate Docker.

```bash
git clone https://github.com/emam26/ReproScout.git
cd ReproScout
```

`uv` is the primary installation path:

| Use case | Command |
| --- | --- |
| CLI only | `uv tool install --editable .` |
| CLI plus the local API/dashboard backend | `uv tool install --reinstall --editable ".[api]"` |
| Development checkout | `uv venv` then `uv pip install -e ".[dev]"` |

Use the API installation command when adding the optional API to an existing CLI-only `uv` tool installation; `--reinstall` refreshes that managed environment with the extra.

For a standard virtual environment instead:

```bash
python -m venv .venv
# Activate .venv for your shell, then choose one:
python -m pip install -e .          # CLI only
python -m pip install -e ".[api]"  # CLI plus local API
python -m pip install -e ".[dev]"  # development dependencies
```

## Quick Start

Start by inspecting the local prerequisites. `doctor` is read-only. `setup --dry-run` describes the onboarding result without changing installation or runtime state.

```bash
reproscout doctor
reproscout setup --dry-run
reproscout setup
reproscout preflight https://github.com/OWNER/REPOSITORY
```

`setup` requires explicit consent before it performs an installation action. A running, healthy Docker runtime is reused rather than reinstalled. Replace `OWNER/REPOSITORY` with the public repository to assess.

A completed preflight writes these artifacts to its run output:

- `environment-report.md` - concise human-readable readiness report.
- `environment-evidence.json` - structured evidence, commands, outcomes, and unresolved findings.
- `agent-handoff.md` - a focused brief for a researcher or coding agent.

Use `reproscout preflight --help` for supported options, including `--ai` and a custom run directory.

## How ReproScout Works

1. It records the repository identity and exact revision used for the assessment.
2. It inspects repository metadata and setup material for Python, package, environment, and system prerequisites.
3. It creates a bounded plan and uses the sandbox only for permitted setup and lightweight verification commands.
4. It evaluates deterministic evidence such as command outcomes, files, and environment checks.
5. It reports verified facts separately from incomplete, blocked, unsafe, or unverified conditions.

The control flow and verifier determine status. Optional LLM reasoning can help interpret instructions and failures, but it is never the authority for a successful environment or experiment.

## Example Outputs

The following is a schematic run-output layout; the exact run directory is configurable:

```text
<run-output>/
|-- environment-report.md
|-- environment-evidence.json
`-- agent-handoff.md
```

| Artifact | Intended use |
| --- | --- |
| `environment-report.md` | Share a short, evidence-grounded environment assessment with a researcher. |
| `environment-evidence.json` | Inspect structured commands, outcomes, checks, and unresolved findings programmatically. |
| `agent-handoff.md` | Give a coding agent the pinned commit, verified environment facts, executed commands, outcomes, and safe next steps. |

For example, a `READY` assessment means the environment checks required by the selected preflight plan were verified. It does not mean that experiments, training, benchmarks, or scientific claims have been reproduced.

## CLI Commands

| Command | Purpose |
| --- | --- |
| `reproscout doctor` | Inspect local prerequisites without making changes. |
| `reproscout setup [--dry-run] [--yes]` | Assess or, with explicit consent, perform supported onboarding actions. |
| `reproscout preflight <github-url>` | Run the safe environment-preflight workflow. |
| `reproscout audit <github-url> --goal "..."` | Explicitly request the separate bounded target-workflow audit path. |
| `reproscout inspect`, `run`, `runs`, `status`, `report` | Inspect, start, and review supported workflow runs. |
| `reproscout cleanup` | Clean ReproScout-owned resources through the controlled lifecycle. |
| `reproscout config` | View or manage supported local configuration. |
| `reproscout serve` | Start the optional loopback-only API service. |

Use `reproscout --help` and the [CLI reference](docs/CLI.md) for the authoritative command contracts and option details.

## Local Dashboard and Public Website

The dashboard is a local development interface. Install the API extra before starting its backend:

```bash
uv tool install --reinstall --editable ".[api]"
reproscout serve
```

In a second terminal, start the frontend from the source checkout:

```bash
cd frontend
npm ci
npm run dev
```

The development dashboard listens on `http://127.0.0.1:5173` and proxies API requests to the loopback API at `http://127.0.0.1:8000`. The backend health endpoint is `http://127.0.0.1:8000/api/v1/health`; it confirms API availability only, not Docker readiness or sandbox security.

The separately built static GitHub Pages site uses the `/ReproScout/` base path and contains no backend job execution. The Pages workflow validates the public build and deploys `frontend/dist`; it is distinct from the local dashboard, and this README does not claim a public deployment is live.

## Evaluation and Testing

ReproScout has unit, offline workflow, Docker integration, and frontend test coverage. Docker integration testing requires a compatible local Docker daemon and is intentionally separate from the offline suite.

Historical project notes record a prior baseline of 296 offline Python tests, 14 Docker tests, and 15 React tests. Those are historical observations, not a claim about the current checkout. The controlled real-world evaluation set, S01-S20, remains marked `NOT_RUN`; public-repository evaluation is incomplete. Native Windows and macOS installation paths also require direct platform verification.

See the [evaluation set](docs/EVALUATION_SET.md) and [roadmap](docs/ROADMAP.md) for the documented scope and outstanding validation work.

## Security and Limitations

- Repositories and their instructions are treated as untrusted input. ReproScout is designed to avoid executing target code directly on the host.
- The Docker sandbox is defense in depth, not a guarantee of perfect isolation. Supported paths configure non-root execution, capability dropping, a read-only root filesystem, resource bounds, controlled networking, workspace validation, and owned-container cleanup.
- Security configuration is not proof of a working sandbox. Readiness requires observed checks; a configured restriction alone does not establish a verified security capability.
- Installation actions require consent. `--dry-run` is designed to avoid installation and runtime-changing actions.
- Secrets and sensitive command output are redacted in reported diagnostics, but users should still avoid placing credentials in target repositories or command arguments.
- The default preflight does not reproduce scientific results. Installing packages successfully is not evidence that a model, metric, benchmark, or paper claim is reproducible.
- The optional API binds to loopback addresses only. Do not expose it through a proxy or public interface without an independent deployment security review.

Review the [Docker sandbox design](docs/DOCKER_SANDBOX.md) before relying on ReproScout for sensitive workloads.

## Documentation

- [Project specification](docs/PROJECT_SPEC.md) and [architecture](docs/ARCHITECTURE.md)
- [CLI reference](docs/CLI.md), [API reference](docs/API.md), and [dashboard guide](docs/DASHBOARD.md)
- [Docker sandbox](docs/DOCKER_SANDBOX.md) and [diagnostics](docs/DIAGNOSTICS.md)
- [Evaluation set](docs/EVALUATION_SET.md) and [roadmap](docs/ROADMAP.md)
- [Contributing guide](CONTRIBUTING.md)

## Contributing and License

Contributions are welcome within the documented scope and safety model; start with the [contributing guide](CONTRIBUTING.md). ReproScout is released under the [MIT License](LICENSE).
