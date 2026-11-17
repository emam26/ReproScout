# Repository Analysis

Phase 5 converts a deterministic `RepositoryManifest` into a focused
`RepositoryAnalysis`. It does not execute repository code, build an execution
plan, search the web, or declare whether a repository is reproducible.

## Deterministic-first extraction

`RepositoryAnalyzer` reads a bounded set of repository-local evidence and uses
standard-library parsers or conservative pattern extraction for:

* `pyproject.toml`, `setup.py`, and `setup.cfg` package metadata;
* `requirements*.txt` and Conda environment files;
* pytest/tox configuration and Make targets;
* Docker base-image, Python, and CUDA hints;
* GitHub Actions Python versions and commands;
* root and nested README, INSTALL, CONTRIBUTING, setup, environment, pipeline,
  quick-start, and usage notes (for example, `docs/setup.md` and
  `docs/pipeline.md`) for documented setup, test, and run commands;
* documented environment-variable names, external assets, checkpoints, and
  explicit GPU requirements.

`setup.py` is never imported or executed. Target dependencies are not installed
during analysis.

## Analysis model and provenance

`RepositoryAnalysis` records project/runtime hints, package manager,
dependencies, install/test/run commands, entrypoints, environment-variable
names, assets, GPU/network requirements, a likely execution target, conflicts,
context-file names, truncated context-file names, and confidence.

Important claims retain `AnalysisEvidence` with one of three provenance values:

```text
DOCUMENTED
DETERMINISTICALLY_DETECTED
LLM_INFERRED
```

Conflicting documentation is retained instead of being silently replaced. For
example, a README Python version that contradicts `requires-python` becomes an
explicit conflict.

## Bounded context

Optional interpretation receives only relevant manifest files. Defaults allow
at most 20 files, 64 KiB per file, and 120,000 total characters. Absolute and
parent-relative paths, symlinks, `.git`, binary files, and oversized files are
excluded. Credential-shaped assignments are redacted before context is built.
Datasets, checkpoints, generated trees, and arbitrary source files are not
collected.

The structural manifest is likewise bounded: it scans at most 20,000 entries,
12 directory levels, 100 setup-documentation files, and 50 dependency files.
Skipped setup-source symlinks, unreadable sources, truncated analysis context,
and exhausted discovery limits are recorded as incomplete discovery. Unrelated
symlinked files are not treated as setup evidence. Preflight can still verify
the sources it did inspect, but it reports `PARTIAL` rather than `READY` when
undiscovered setup requirements could remain.

## Optional LLM interpretation

Deterministic analysis runs without an LLM. A Phase 4 `LLMProvider` is consulted
only when the likely execution target remains ambiguous or deterministic and
documented evidence conflicts. The response must use the
`supplement_analysis` action and its arguments must validate as the narrow
`AnalysisInference` schema.

LLM inference can add explicitly marked hypotheses, but it cannot overwrite a
stronger deterministic project type, execution target, GPU conclusion, or asset
finding. Normal tests use `MockLLMProvider`; no live provider or API key is
required.

## Limitations

The extractor is intentionally conservative. It does not fully interpret
arbitrary shell scripts, dynamic `setup.py` logic, YAML semantics, or prose. It
does not perform web search, download assets, create a reproduction plan, run
commands, or verify outcomes. Those are separate later phases.
