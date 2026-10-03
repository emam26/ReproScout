# Phase 18 — System Evaluation

Phase 18 compares typed observed run facts with the Phase 17 evaluation
contracts. `EvaluationRunner` calls a supplied system adapter once per case and
returns an `EvaluationReport`; it does not execute repository code itself.

The report includes:

* exact status and verification-level accuracy,
* expected/observed status counts and a confusion matrix,
* failure-category counts,
* repair-case count and repair-success rate,
* human-intervention rate,
* optional stage success rates,
* mean runtime, total bounded agent steps, and LLM calls,
* explicit per-case mismatches.

Missing, duplicate, or foreign observations are errors. Case order is stable,
and all aggregation is deterministic. The evaluator does not treat an LLM
summary as evidence; the adapter must provide machine-observed status and
verification facts produced by ReproScout’s existing verifier and state.

Phase 19 adds a predeclared protocol with documented-only, deterministic-only,
and bounded-agent baselines plus paired no-LLM, no-repair, and no-clean-room
ablations. Comparisons report descriptive metric deltas and per-case status
changes only; they do not claim causal effects or statistical significance from
one finite controlled set.
