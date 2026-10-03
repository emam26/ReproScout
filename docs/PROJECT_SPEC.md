# ReproScout Project Specification

## 1. Product

**Name:** ReproScout

**Purpose:** Autonomous reproducibility auditing for open-source AI/ML repositories.

Given a public repository, ReproScout attempts to determine whether the project can be reproduced from a clean environment and records exactly:

* what worked,
* what failed,
* why it failed,
* what repairs were attempted,
* what changes were required,
* whether the result was objectively verified.

---

# 2. Problem

Open-source repositories frequently fail on new machines because of issues such as:

* undocumented Python versions,
* stale dependencies,
* package conflicts,
* CUDA incompatibilities,
* missing checkpoints,
* dead URLs,
* missing directories,
* missing environment variables,
* incorrect README commands,
* deprecated APIs,
* undocumented setup steps,
* unavailable datasets.

A developer normally has to manually investigate and repair these problems.

ReproScout should automate as much of this workflow as safely possible.

---

# 3. Target Users

Initial users:

* AI/ML researchers,
* research engineers,
* students,
* ML engineers,
* open-source maintainers.

Possible future users:

* conference artifact reviewers,
* companies evaluating open-source dependencies,
* reproducibility researchers.

---

# 4. Initial Input

V1 input:

```text
GitHub repository URL
```

Optional goal:

```text
auto
install
tests
demo
```

Future input:

```text
research paper
expected result
specific experiment
hardware target
```

---

# 5. Expected Final Output

Each reproduction run should eventually create something similar to:

```text
runs/<run-id>/
├── report.md
├── run.json
├── events.jsonl
├── commands.jsonl
├── patches.diff
├── environment.json
├── reproduce.sh
├── logs/
│   ├── setup.log
│   ├── execution.log
│   └── tests.log
└── workspace/
```

Possible later artifacts:

```text
Dockerfile.reproduced
requirements.lock
environment.yml
```

---

# 6. Final Statuses

## REPRODUCED

The requested reproduction goal was objectively verified.

Example:

```text
environment built
target executed
expected output created
tests passed
```

---

## PARTIAL

Important parts worked, but full reproduction was not achieved.

Example:

```text
application runs
18/20 tests pass
```

---

## BLOCKED

Execution cannot continue because of an external limitation.

Examples:

```text
private dataset
missing checkpoint
dead external resource
required credentials
unsupported hardware requirement
```

---

## FAILED

The agent exhausted reasonable bounded repair attempts without success.

---

## UNSAFE

The operation was stopped because it violated safety policy.

---

# 7. Verification Levels

Track a more granular verification level:

```text
L0 — repository successfully inspected
L1 — environment successfully created
L2 — intended project/demo successfully executes
L3 — official test suite successfully executes
L4 — reported research result reproduced
```

V1 focuses on:

```text
L0
L1
L2
L3
```

L4 belongs to future paper reproduction.

---

# 8. Core Workflow

```text
Repository URL
      ↓
INTAKE
      ↓
Clone repository
      ↓
Capture exact commit
      ↓
ANALYZE
      ↓
Create repository manifest
      ↓
Understand documentation
      ↓
PLAN
      ↓
Create reproduction plan
      ↓
CREATE SANDBOX
      ↓
SETUP
      ↓
EXECUTE
      ↓
 ┌────┴────┐
success   failure
   │         ↓
   │      OBSERVE
   │         ↓
   │      DIAGNOSE
   │         ↓
   │    PROPOSE ACTION
   │         ↓
   │     POLICY CHECK
   │         ↓
   │       EXECUTE
   │         ↓
   │      UPDATE STATE
   │         ↓
   └────── RETRY
             ↓
           VERIFY
             ↓
           REPORT
```

---

# 9. Repository Intake

For every repository capture:

* repository URL,
* owner/name,
* exact commit SHA,
* branch,
* repository size where available,
* language hints,
* license where present.

Detect important files such as:

```text
README*
INSTALL*
CONTRIBUTING*
requirements*.txt
pyproject.toml
setup.py
setup.cfg
environment.yml
Dockerfile*
Makefile
tox.ini
pytest.ini
.github/workflows/*
tests/
test/
examples/
demo/
scripts/
configs/
```

This step should be deterministic and should not require an LLM.

---

# 10. Repository Manifest

Example:

```json
{
  "repository": "owner/project",
  "commit_sha": "abc123",
  "important_files": [
    "README.md",
    "requirements.txt",
    "tests/test_model.py"
  ],
  "dependency_files": [
    "requirements.txt"
  ],
  "has_tests": true,
  "has_dockerfile": false
}
```

---

# 11. Repository Analysis

Repository analysis combines:

1. deterministic inspection,
2. LLM interpretation where required.

Desired structured output:

```json
{
  "project_type": "machine_learning",
  "language": "python",
  "python_versions": ["3.10"],
  "frameworks": ["pytorch"],
  "dependency_files": ["requirements.txt"],
  "installation_commands": [
    "pip install -r requirements.txt"
  ],
  "test_commands": [
    "pytest"
  ],
  "demo_commands": [
    "python demo.py"
  ],
  "required_assets": [],
  "gpu_required": false,
  "network_required": true,
  "confidence": 0.85
}
```

Important conclusions should include evidence from repository files when practical.

---

# 12. Reproduction Plan

Before execution, create an explicit plan.

Example:

```json
{
  "goal": "demo",
  "steps": [
    {
      "type": "create_environment",
      "python": "3.10"
    },
    {
      "type": "install_requirements",
      "path": "requirements.txt"
    },
    {
      "type": "run_command",
      "command": "python demo.py"
    }
  ]
}
```

Plans may change during execution.

Every change should be recorded.

---

# 13. State Model

The agent must not depend on model conversation memory.

Persist structured application state.

Important conceptual models:

```text
RunState
RepositoryManifest
RepositoryAnalysis
ReproductionPlan
Attempt
ToolCall
ToolResult
Diagnosis
RepairAction
VerificationResult
RunOutcome
```

Important fields:

```text
run_id
repo_url
commit_sha
goal
current_stage
attempt_count
llm_call_count
commands_executed
files_modified
environment_changes
previous_errors
current_error
terminal_status
created_at
updated_at
```

Initial persistence should be simple.

SQLite is preferred when persistence is introduced.

---

# 14. Event Log

Important actions should produce events.

Examples:

```text
RUN_CREATED
REPO_CLONED
REPO_ANALYZED
PLAN_CREATED
SANDBOX_CREATED
COMMAND_STARTED
COMMAND_FINISHED
SETUP_FAILED
EXECUTION_FAILED
ERROR_DIAGNOSED
REPAIR_PROPOSED
REPAIR_APPLIED
FILE_MODIFIED
ROLLBACK
TESTS_RUN
VERIFICATION_COMPLETED
RUN_FINISHED
```

Prefer append-only event records.

This supports:

* debugging,
* observability,
* evaluation,
* reproducibility.

---

# 15. Tool Layer

The model interacts with the application through controlled tools.

Potential repository tools:

```text
list_files(path)
read_file(path)
search_repo(query)
get_repo_manifest()
```

Potential Git tools:

```text
get_status()
get_diff()
restore_file(path)
```

Potential sandbox tools:

```text
create_sandbox(spec)
run_command(command, timeout)
```

Potential environment tools:

```text
install_requirements(path)
install_package(name, version)
```

Potential verification tools:

```text
run_tests()
check_file_exists(path)
inspect_exit_code()
```

Later:

```text
download_asset()
edit_file()
apply_patch()
rollback()
```

Tool arguments and results should use typed schemas.

---

# 16. LLM Responsibilities

The LLM may handle:

* interpreting README instructions,
* resolving ambiguous setup documentation,
* identifying likely entrypoints,
* interpreting errors,
* choosing useful files to inspect,
* forming debugging hypotheses,
* proposing minimal repairs,
* selecting the next controlled action,
* summarizing evidence.

The LLM should NOT be responsible for:

* cloning,
* checking file existence,
* checking exit codes,
* timeout enforcement,
* retry counting,
* test-result arithmetic,
* sandbox policy,
* final objective verification.

---

# 17. LLM Interface

The application should use a provider-independent interface.

Conceptually:

```python
class LLMProvider:
    async def decide(
        self,
        context,
        available_tools,
    ): ...
```

Future provider structure:

```text
llm/
├── base.py
└── providers/
    ├── gemini.py
    └── groq.py
```

The rest of ReproScout should not know which provider is active.

---

# 18. Structured Agent Decision

Model output should eventually resemble:

```json
{
  "summary": "The current NumPy version is incompatible with deprecated np.float usage.",
  "action": "install_package",
  "arguments": {
    "name": "numpy",
    "version": "1.23.5"
  },
  "expected_effect": "Restore compatibility with the codebase.",
  "confidence": 0.84
}
```

Only concise reasoning summaries are needed.

Do not require hidden chain-of-thought.

---

# 19. Repair Priority

Prefer repairs roughly in this order:

```text
1. Correct documented invocation
2. Gather missing information
3. Runtime invocation change
4. Python/runtime version change
5. Dependency version fix
6. Configuration/path fix
7. Recover documented asset
8. Minimal compatibility patch
9. Source-code modification
```

Source changes should be more conservative than environment changes.

---

# 20. Failure Detection

Do not repeat the same failed strategy indefinitely.

Failures may be fingerprinted using:

```text
stage
command
exit code
exception type
important stderr lines
```

If the same action repeatedly leads to the same failure:

* gather different evidence,
* choose another strategy,
* or stop.

---

# 21. Sandbox

All target repository execution must eventually occur inside isolation.

MVP sandbox:

```text
Docker
```

Desired properties:

* disposable container,
* no host secrets,
* no SSH keys,
* no `.env`,
* no Docker socket,
* controlled workspace,
* CPU limits,
* memory limits,
* process limits,
* command timeout,
* minimal Linux capabilities.

Where compatible, prefer protections such as:

```text
--cap-drop=ALL
--security-opt=no-new-privileges
```

Docker should not be presented as perfect protection against hostile code.

Stronger isolation may be investigated later.

---

# 22. Safety Policy

Reject or stop unsafe behavior involving:

* host filesystem access,
* secrets,
* credentials,
* privileged containers,
* Docker socket access,
* modifying host system files,
* unreasonable resource usage,
* execution outside sandbox.

The LLM cannot override safety policy.

---

# 23. Resource Limits

A run should eventually support configuration for:

```text
max_agent_steps
max_llm_calls
max_repairs
max_command_seconds
max_total_runtime
max_download_bytes
max_log_bytes
max_cpu
max_memory
```

Exact defaults should be determined experimentally.

---

# 24. Verification

Never define success as:

```text
"The LLM thinks it worked."
```

Use objective evidence.

Examples:

## Installation

```text
exit_code == 0
```

## Demo

```text
exit_code == 0
AND expected artifact/output exists
```

## Tests

Track:

```text
passed
failed
skipped
errors
```

The same evidence should always produce the same verification result regardless of model opinion.

---

# 25. Final Report

Human-readable report should eventually include:

```text
Repository
Commit SHA
Goal
Final status
Verification level

Detected environment
Documented installation procedure
Actual procedure used

Problems discovered
Repairs performed
Files changed
Dependencies changed

Test results
Execution results

Remaining blockers

Exact reproduction steps
```

Every important claim should come from stored run evidence.

---

# 26. Machine-Readable Result

Also generate a structured result.

Example:

```json
{
  "status": "PARTIAL",
  "verification_level": 2,
  "environment_created": true,
  "target_executed": true,
  "tests": {
    "passed": 18,
    "failed": 2
  },
  "repair_count": 3,
  "human_intervention": false
}
```

---

# 27. CLI

The first public interface should be a CLI.

Future usage:

```bash
reproscout run https://github.com/owner/project
```

Possible goal:

```bash
reproscout run https://github.com/owner/project --goal tests
```

Report inspection:

```bash
reproscout report <run-id>
```

Do not build a web frontend before the core engine works.

---

# 28. Initial Technology Choices

Core:

```text
Python
Pydantic
Git
pytest
Docker
SQLite
```

Initial interface:

```text
Typer CLI
```

Later:

```text
FastAPI
web dashboard
```

LLM:

```text
provider abstraction
hosted free-tier model initially
```

Do not couple core architecture to a particular model provider.

---

# 29. Suggested Long-Term Source Layout

Do not create every folder immediately.

Create modules only when their roadmap phase begins.

Long-term direction:

```text
src/reproscout/
├── cli.py
├── config.py
│
├── core/
│   ├── state.py
│   ├── actions.py
│   ├── events.py
│   └── orchestrator.py
│
├── repo/
│   ├── clone.py
│   ├── manifest.py
│   └── analyzer.py
│
├── sandbox/
│   ├── base.py
│   └── docker.py
│
├── tools/
│   ├── base.py
│   ├── files.py
│   ├── git.py
│   ├── shell.py
│   └── verification.py
│
├── llm/
│   ├── base.py
│   └── providers/
│
├── agent/
│   ├── planner.py
│   ├── debugger.py
│   └── loop.py
│
├── verification/
│   └── verifier.py
│
├── reporting/
│   └── report.py
│
└── safety/
    └── policy.py
```

Do not create unnecessary empty abstractions early.

---

# 30. Evaluation

ReproScout must eventually be evaluated against real repositories.

Start with:

```text
20 repositories
```

Later:

```text
50+ repositories
```

Track:

```text
repository intake success
analysis success
environment setup success
execution success
test execution success
full reproduction rate
partial reproduction rate
blocked rate
failure rate
repair success rate
human intervention rate
agent steps
LLM calls
runtime
```

Categorize common failures:

```text
dependency incompatibility
Python incompatibility
CUDA/hardware
missing asset
broken URL
documentation error
configuration problem
source incompatibility
private resource
unknown
```

---

# 31. Future Research-Paper Extension

After repository reproduction is reliable:

```text
Research paper
       +
GitHub repository
       ↓
Extract claimed experiment
       ↓
Locate matching code/config
       ↓
Execute experiment
       ↓
Compare reported result
       ↓
Analyze discrepancy
       ↓
Claim-level reproduction report
```

This is not part of V1.

---

# Product North Star

The final system should answer:

> Can this open-source project actually be reproduced from a clean environment, and what concrete evidence supports that conclusion?
