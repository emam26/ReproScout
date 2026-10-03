# Diagnostic & Verification Foundation

Phase 7.5 converts raw execution failures into compact deterministic evidence.
It does not ask an LLM to parse logs, resolve dependencies, inspect the runtime,
or verify success, and it does not perform a repair.

## Failure evidence

`FailureClass` covers Python/runtime incompatibility, dependency and import
failures, build tools and system libraries, CUDA/GPU/resource failures, missing
files/assets, HTTP/auth/network failures, permissions, configuration, tests,
timeouts, and `UNKNOWN`. Rules only classify recognizable deterministic
evidence. Ambiguous output remains `UNKNOWN`.

`normalize_failure` selects traceback and relevant error neighborhoods, redacts
credentials, normalizes volatile paths, and enforces line and character bounds.
The compact record retains a reference to the bounded full run log rather than
copying thousands of lines into later model context.

## Dependency intelligence

Requirement strings, versions, specifiers, extras, and markers use the
`packaging` library. Pip installation reports and `pip inspect` JSON are parsed
as machine-readable evidence; `pip check` findings are normalized separately.
`PipDiagnosticRunner` accepts only the sandbox abstraction and invokes:

```text
python -m pip install --dry-run --ignore-installed --report - -r <path>
python -m pip inspect
python -m pip check
```

These commands are intended for an already-created Docker sandbox. They never
install or inspect target-project dependencies in the host ReproScout
environment.

Python imports are parsed with `ast` under file/count limits and compared with
declared distributions. Known import/distribution differences such as `cv2` →
`opencv-python`, `PIL` → `Pillow`, and `sklearn` → `scikit-learn` are explicitly
marked as heuristic mappings; they never authorize installation by themselves.

## Environment fingerprint

The fingerprint command executes through `Sandbox` and captures the repository
commit, configured image/digest, OS, architecture, Python and pip versions,
installed packages, configured CPU/memory, GPU visibility, CUDA/compiler facts
when available, network mode, and non-sensitive environment-variable names.
Secret-shaped environment names and all environment values are excluded.

## Evidence and repetition control

`EvidenceBuilder` assembles only the normalized failure, relevant dependency and
documentation evidence, a compact environment summary, selected source/config
snippets, and prior attempts/repairs. It applies per-item, item-count, and total
character bounds before a future LLM call and redacts credentials again.

Repeated-failure signatures hash the failure class, normalized command/error,
and relevant environment identity. Volatile paths, line numbers, process IDs,
and long hexadecimal identifiers do not create artificial new failures.

## Assets and verification contract

The bounded repository scan detects likely checkpoints, datasets, external
asset URLs, Git LFS pointers, submodules, and obvious DVC indicators. It performs
no URL probing or downloads and strips URL credentials and query strings.

`VerificationContract` defines finite objective targets such as installation
success, command exit, test execution, expected artifacts, and output
conditions. Phase 7.5 defines what evidence would prove a target; it does not
evaluate a final `REPRODUCED`, `PARTIAL`, `BLOCKED`, `FAILED`, or `UNSAFE`
classification.

No YAML dependency was added: this phase does not require structural YAML
loading. If future analysis does, untrusted YAML must be size-bounded and parsed
with `yaml.safe_load`.
