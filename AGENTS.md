# ReproScout — Agent Instructions

## Project Mission

ReproScout is an autonomous reproducibility auditor for open-source software, initially focused on Python AI/ML repositories.

Given a public GitHub repository, ReproScout should determine whether the project can be reproduced from a clean environment using its published instructions.

The system should eventually:

1. inspect the repository,
2. understand the documented setup,
3. determine the intended execution workflow,
4. create an isolated environment,
5. install dependencies,
6. execute the project,
7. observe failures,
8. diagnose failures,
9. apply bounded and reversible repairs,
10. retry,
11. objectively verify the result,
12. generate an evidence-backed reproducibility report.

The goal is NOT to build a generic coding agent.

---

## Read Before Working

For substantial work, inspect:

* `docs/PROJECT_SPEC.md` — architecture and product source of truth.
* `docs/ROADMAP.md` — implementation phases and current progress.

Do not implement future roadmap phases unless explicitly requested.

---

# Core Architecture Principle

Always preserve this separation:

```text
LLM = reasoning
Tools = execution
State machine = control
Verifier = truth
Docker = isolation
```

The LLM must never be treated as the authority for whether reproduction succeeded.

---

# Engineering Principles

## 1. Deterministic Software Before LLM Reasoning

If ordinary code can reliably perform a task, use ordinary code.

Examples:

* cloning repositories,
* listing files,
* parsing paths,
* checking file existence,
* checking exit codes,
* hashing,
* tracking attempts,
* running tests,
* Git diff generation,
* timeout enforcement.

Use the LLM only where interpretation or reasoning is useful.

Examples:

* interpreting README instructions,
* identifying likely entrypoints,
* diagnosing unusual errors,
* selecting what file to inspect next,
* proposing a minimal repair,
* summarizing evidence.

---

## 2. Never Trust the LLM as the Verifier

The LLM may suggest that something succeeded.

Application code decides whether it actually succeeded.

Verification should use evidence such as:

* exit codes,
* passing tests,
* expected files,
* expected output,
* configured verification rules.

Never use an LLM statement such as "the project works" as proof.

---

## 3. Target Repository Code Is Untrusted

Never execute cloned repository code directly on the host operating system.

Execution must eventually occur through the sandbox abstraction.

Never expose:

* `.env`,
* API keys,
* SSH keys,
* Git credentials,
* unrelated host files,
* Docker socket,
* cloud credentials.

---

## 4. Every Change Must Be Auditable

Preserve:

* repository URL,
* original commit SHA,
* commands executed,
* environment changes,
* dependency changes,
* source/config changes,
* Git diff,
* errors,
* retries,
* verification evidence.

Never silently change the target repository.

---

## 5. Agent Behavior Must Be Bounded

Every reproduction run must eventually have limits for:

* maximum agent steps,
* maximum LLM calls,
* maximum repair attempts,
* command timeout,
* total runtime,
* download size,
* log size,
* CPU usage,
* memory usage.

Do not create infinite retry loops.

---

## 6. Prefer Minimal Repairs

When something fails, prefer the smallest valid intervention.

Preferred order:

1. correct invocation,
2. gather missing information,
3. runtime/environment adjustment,
4. dependency adjustment,
5. configuration/path fix,
6. recover documented asset,
7. minimal compatibility patch,
8. source modification only when necessary.

---

# V1 Scope

Initially support:

* public Git repositories,
* Python projects,
* AI/ML repositories,
* `requirements.txt`,
* `pyproject.toml`,
* `setup.py`,
* `setup.cfg`,
* `environment.yml`,
* Dockerfile detection,
* README-driven setup,
* pytest,
* lightweight examples/demos,
* primarily CPU-runnable projects.

---

# V1 Non-Goals

Do not prioritize yet:

* frontend UI,
* LangChain,
* LangGraph,
* multi-agent systems,
* Kubernetes,
* distributed execution,
* multi-GPU training,
* giant datasets,
* arbitrary operating systems,
* paper-result reproduction,
* autonomous pull requests.

Build the core engine first.

---

# State Machine

The core workflow should eventually follow explicit states:

```text
INTAKE
→ ANALYZE
→ PLAN
→ SETUP
→ EXECUTE
→ DEBUG
→ VERIFY
→ REPORT
→ DONE
```

Possible terminal outcomes:

```text
REPRODUCED
PARTIAL
BLOCKED
FAILED
UNSAFE
```

Do not create unrestricted autonomous behavior outside this control flow.

---

# Tool Philosophy

The model should interact with the system through controlled tools.

Potential tool categories:

```text
clone_repository
list_files
read_file
search_repository
inspect_environment_files
create_sandbox
run_sandboxed_command
install_dependency
edit_file
apply_patch
download_asset
run_tests
get_git_diff
rollback
inspect_logs
```

Tool inputs and outputs should use typed schemas.

Do not expose unrestricted host shell execution to the LLM.

---

# LLM Rules

Do not send entire repositories to the model unnecessarily.

Prefer small relevant contexts containing:

* repository summary,
* relevant files,
* current error,
* command output,
* previous attempts,
* current state,
* available actions.

Important information should live in application state, not only in model conversation history.

Prefer structured model outputs.

Do not require or store hidden chain-of-thought.

Store only concise decision summaries when useful.

---

# Provider Independence

The project must not be tightly coupled to one LLM provider.

Provider-specific implementation belongs behind an interface.

Example future structure:

```text
llm/
├── base.py
└── providers/
    ├── gemini.py
    └── groq.py
```

Business logic must not directly depend on Gemini, Groq, OpenAI, or any other provider.

Never commit API keys.

Use environment variables.

Commit `.env.example`.

Never commit `.env`.

---

# Testing Rules

Use:

* unit tests for deterministic logic,
* fixture repositories for repository analysis,
* mocked LLM responses for agent tests,
* controlled integration repositories for Docker tests,
* real GitHub repositories only for explicit end-to-end evaluation.

Normal unit tests should not require:

* network access,
* paid APIs,
* external services.

Every meaningful deterministic feature should have tests.

---

# Coding Guidelines

* Use Python.
* Prefer simple implementation over unnecessary frameworks.
* Use type hints.
* Use typed data models for important state.
* Keep modules focused.
* Avoid global mutable state.
* Preserve useful error information.
* Fail explicitly rather than silently.
* Keep prompts separate from business logic.
* Log meaningful events.
* Avoid premature abstractions.
* Avoid unnecessary dependencies.

Do not add a dependency unless it clearly improves the project.

---

# Codex Working Rules

Work one roadmap phase at a time.

Before editing:

1. read this file,
2. inspect `docs/PROJECT_SPEC.md`,
3. inspect `docs/ROADMAP.md`,
4. inspect existing code,
5. identify the smallest coherent change.

After editing:

1. run relevant tests,
2. run configured lint checks,
3. fix failures caused by the change,
4. summarize what changed,
5. update `docs/ROADMAP.md` only when a milestone is actually completed.

Do not opportunistically implement later phases.

Keep diffs focused and reviewable.

---

# Priority Order

When requirements conflict, prioritize:

1. safety,
2. correctness,
3. reproducibility,
4. testability,
5. simplicity,
6. convenience.

---

# Definition of Done

A feature is not complete merely because code exists.

Where relevant it should have:

* implementation,
* tests,
* failure handling,
* typed interfaces,
* useful logs,
* no leaked secrets,
* documentation updates,
* no unnecessary unrelated changes.

---

# Product North Star

ReproScout should ultimately answer:

> Can a new user reproduce this open-source project from a clean environment, and if not, exactly what prevents reproduction?

Every major engineering decision should support answering that question reliably and with evidence.
