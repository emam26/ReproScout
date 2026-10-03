# Phase 16 — Observability

ReproScout derives observability from the existing append-only `Event` stream.
It does not add a second logging system, external metrics service, or target
execution path.

## Projection

`build_run_observability(events)` returns a typed `RunObservability` containing:

* a stable sequence-ordered timeline with bounded summaries,
* total run duration and per-stage durations,
* attempt and controlled-repair counts,
* LLM diagnosis calls and input/output token totals,
* failure-category counts,
* optional objective verification level and final reproduction status,
* observed Docker container IDs and network modes.

The optional verification and status values are passed from their deterministic
engines. They are never inferred from an LLM statement. Token totals are marked
incomplete when a provider did not return one of the token fields.

Timeline entries intentionally omit raw command output, arguments, and event
payloads. They contain only event type, stage, timestamp, sequence, and a
redacted bounded summary. This keeps the projection suitable for evaluation
queries without duplicating sensitive run logs.

## Evaluation queries

`ObservabilityIndex` is a small in-memory query helper for a caller that is
already evaluating a finite set of runs. It can select projections by final
status or failure category. SQLite remains the source of truth for state and
events; the index is not a persistence backend.

## Limits and failure handling

The builder requires one run’s contiguous event sequence and rejects mixed run
IDs or decreasing timestamps. It sorts by persisted sequence before projection,
preserves event order, and never executes target code.
