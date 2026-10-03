# Real-repository benchmark

The pre-v0.1 benchmark harness is intentionally separate from the 20-case
offline fixture set. It records observations for public, pinned, CPU-runnable
Python repositories without fabricating expected outcomes.

The checked-in manifest is currently empty because no exact repository revision
has been selected and executed in this environment. Add a case only after
checking that it has:

- a public HTTPS GitHub URL and full 40-character commit SHA;
- a bounded CPU/Python workflow with a clear install, test, or demo goal;
- no private credentials, large model/checkpoint download, or unbounded service;
- an execution budget suitable for a local Docker run.

The typed schema and runner are available through `reproscout.evaluation`:

```python
from reproscout.evaluation import load_benchmark_manifest, run_benchmark

manifest = load_benchmark_manifest()
report = run_benchmark(manifest, adapter)
```

An adapter must return observed status, verification level, failure category,
clean-room result, runtime, attempts, LLM calls, and optional token usage. The
`false_reproduced` field remains unknown until an independent adjudication is
available. Do not treat fixture labels as real-repository observations.
