# LLM Providers

Phase 4 adds a provider-independent reasoning boundary. ReproScout core code
submits an `LLMRequest` to the `LLMProvider` interface and receives a validated
`LLMResponse` containing an `AgentDecision`. Provider adapters cannot move the
run state machine, execute tools, or determine whether reproduction succeeded.

## Providers

The package includes:

* `MockLLMProvider` for deterministic offline tests;
* `GeminiProvider` using the Gemini REST API;
* `GroqProvider` using the Groq OpenAI-compatible REST API.

Both hosted adapters use the standard library through an injectable async HTTP
transport, so Phase 4 adds no provider SDK or runtime dependency. There are
currently no provider-specific optional dependency groups to install. Provider
SDK extras can be introduced later only if they offer a demonstrated benefit.

Provider and model selection are explicit. ReproScout does not automatically
fail over between Gemini and Groq because an unrecorded provider change would
harm reproducibility and debugging.

## Configuration

Configure hosted providers with environment variables:

```text
LLM_PROVIDER=gemini  # mock, gemini, or groq
LLM_MODEL=<provider-model-id>
GEMINI_API_KEY=<credential>
GROQ_API_KEY=<credential>
```

Only the key for the selected provider is required. Credentials are represented
as Pydantic `SecretStr` values and are never included in requests, responses,
usage metadata, exceptions, state context, event payloads, tool records, or
logs. Credential-shaped keys in durable JSON records are rejected.

No live key is needed for normal development or tests. Do not put credentials
in committed files; `.env` remains ignored.

## Structured output

`AgentDecision` contains:

```text
summary
action
arguments
expected_effect
confidence
```

The summary is a concise audit rationale, not hidden chain-of-thought. Gemini
and Groq are asked for provider-native JSON-schema output, but provider promises
are not trusted: every decision is independently validated by Pydantic with
unknown fields forbidden. Empty output, malformed JSON, invalid actions,
out-of-range confidence, and schema mismatches fail with
`InvalidProviderResponseError` and never become executable actions.

## Errors and retries

Provider timeouts, network failures, rate limits, and server errors are
translated into typed provider-independent exceptions. Only transient errors
are retried, using a finite exponential-backoff policy with at most five
configured attempts. Authentication/client errors and invalid structured
output are not retried. Provider response bodies are excluded from API-error
messages to avoid leaking sensitive diagnostics.

Usage metadata records provider, model, latency, and optional input/output token
counts. Hosted response parsing tolerates missing usage counts but not missing
decision content.

## Testing and limitations

All normal tests are offline. They use `MockLLMProvider` or injected fake HTTP
transports to cover provider selection, strict schemas, malformed output,
missing credentials, error translation, usage metadata, secret protection, and
retry limits.

Live Gemini and Groq behavior is not exercised unless a user separately
provides credentials and opts into a future live-test path. Phase 4 does not
perform repository analysis, planning, autonomous execution, provider failover,
or final verification.
