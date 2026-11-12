# Phase 17 — Evaluation Set

ReproScout now ships a versioned offline evaluation set with 20 controlled
fixture contracts. The set is loaded by `load_evaluation_set()` and validated by
strict Pydantic models before a later evaluator can use it.

## Design

Each case records:

* stable case identity and normalized fixture path,
* an optional scenario identifier (`S01`-style) when a case is mapped to the
  v1 scenario matrix,
* provenance (`CONTROLLED_FIXTURE` in this version),
* user goal and bounded description,
* evaluation stratum,
* expected final status and verification level,
* expected failure class where applicable,
* whether network access or a bounded repair is part of the contract,
* unique tags for stratified analysis.

The cases cover positive controls and failure regimes: successful demos and
tests, dependency and Python-runtime failures, missing assets, network and
credential blockers, configuration and test/output failures, timeouts, unsafe
commands, repair success and policy stops, documentation gaps, and clean-room
state leakage.

The required v1 S01-S20 scenario register is packaged separately as
`scenario_matrix.json` and loaded with `load_scenario_matrix()`. Its entries
are deliberately initialized as `NOT_RUN`; links to an existing contract do
not mean that the scenario has passed. Scenarios that require an additional
fixture or a Docker/manual failure record say so in their notes.

## Scientific and operational boundaries

The JSON file contains contracts, not observed results. It does not claim that
any case has been reproduced, and it does not execute fixture code when loaded.
This prevents label leakage into a future evaluator and keeps ordinary tests
offline. Phase 18 can run the cases through the same controlled pipeline and
record observed outcomes separately from these expected labels.

## System-evaluation boundary

The Phase 18 `EvaluationRunner` accepts a typed adapter for the actual system.
It invokes that adapter once per case in stable order, rejects missing,
duplicate, or foreign observations, and computes status/verification accuracy,
confusion matrices, failure strata, repair success, intervention rate, stage
success, runtime, agent steps, and LLM calls. The runner is an evaluator, not a
second executor or verifier.

The controlled set is deliberately deterministic and inspectable. It is not a
substitute for a later public-repository evaluation: external repositories need
pinned commits, access and license checks, resource budgets, and a predeclared
protocol before they can support generalization claims.

Adapters can attach the observed fixture or repository, exact source commit,
commands, dependency and security findings, Docker outcome, artifact paths,
execution timestamp, and bounded environment facts to each observation. These
fields are optional because the checked-in evaluation set is metadata only;
they must be populated from the actual run by an owner-run evaluation adapter.
