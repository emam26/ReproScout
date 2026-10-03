# Architecture overview

ReproScout is a bounded reproducibility auditor rather than a general coding
agent. Its control flow is explicit:

```text
INTAKE → ANALYZE → PLAN → SETUP → EXECUTE → DEBUG → VERIFY → REPORT → DONE
```

## Components

* **Intake** validates public GitHub URLs, clones with sterile Git settings,
  captures the exact commit, and builds a structural manifest.
* **Analysis** reads bounded relevant context and produces deterministic facts
  first. Optional LLM interpretation is provider-independent and supplements
  ambiguity only.
* **Planning** converts facts and evidence provenance into a finite,
  command-safe reproduction plan.
* **Sandbox** owns disposable Docker containers with labels, dropped
  capabilities, resource limits, workspace bounds, and explicit network mode.
* **Execution** runs plan steps sequentially and persists attempts, tool calls,
  outputs, failures, and cleanup evidence through the SQLite state store.
* **Diagnosis and repair** normalize failures, build bounded evidence, and
  expose typed policy-checked repair experiments. A diagnosis is a proposal, not
  proof; a repair experiment is not final reproduction evidence.
* **Verification** evaluates only recorded execution, environment, output, and
  artifact facts. It never executes a target command or accepts an LLM claim as
  proof.
* **Clean-room rerun** copies a workspace into a new bounded directory and uses
  a newly created Docker sandbox before `REPRODUCED` can be reported.
* **Reporting** writes schema-versioned JSON/Markdown and only real optional
  artifacts, with secret checks at persistence boundaries.
* **Interfaces** include the public CLI, optional local FastAPI control API, and
  local Vite/React dashboard. They call shared application services rather than
  implementing separate business logic.

## Trust boundaries

Repository files, README text, logs, model outputs, and external assets are
untrusted. The LLM can reason about bounded context but cannot invoke an
arbitrary tool or establish a final status. The verifier and persisted evidence
remain authoritative. The API and dashboard never receive provider credentials.

## Persistence

Each configured runs directory contains a SQLite state database and per-run
artifacts. The database is the control-plane source of truth for state and
append-only events. Reports and plans are derived artifacts used for human
inspection and safe clean-room replay; they are not a second state machine.
