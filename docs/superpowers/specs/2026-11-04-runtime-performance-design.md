# Runtime Performance Design

## Intent

Reduce avoidable latency, resource spikes, container size, and browser work without changing
public HTTP/Python contracts or financial results. The work applies the optimization review from
2026-11-04 and adds regression coverage for every behavior change.

## Goals

- Stop the flow editor from feeding unchanged documents back through React state.
- Bound persona LLM calls globally per compiled graph while retaining parallel execution.
- Fetch independent backtest histories concurrently with a provider-friendly bound.
- Reuse HTTP connections within a run and close every owned client deterministically.
- Replace retention's per-thread global expiry scan with a targeted recheck.
- Avoid a redundant run lookup for events when the caller already knows the thread.
- Keep TensorFlow out of the default application install while retaining an explicit ML extra.
- Split non-default browser screens and serve hashed assets with compression and immutable caching.
- Clear the existing Ruff `PERF401` findings without changing behavior.

## Non-goals

- No API or persisted-schema migration.
- No change to analyst selection, scoring, financial calculations, or event payloads.
- No removal of LSTM or other optional algorithms.
- No provider-specific adaptive rate-control algorithm; limits remain deterministic configuration.
- No broad restructuring of the server or React state architecture.

## Constraints

- Python remains 3.12 and strict mypy-compatible.
- The existing offline test suite must remain deterministic.
- Request order and result order remain stable even when work executes concurrently.
- A run owns only the integration clients created for that run; shared clients such as the paper
  broker must not be closed with the run.
- Retention must recheck expiry after locks are acquired and before checkpoint deletion.
- The default Docker application retains all non-ML quantitative features.

## Approaches

### Selected: bounded, lifecycle-aware incremental changes

Add small boundaries at the existing hot paths: stable React callbacks, semaphores around LLM and
market-data fan-out, an explicit `RunContext` close lifecycle, targeted storage queries, and lazy
frontend imports. This preserves contracts and makes every change independently testable.

### Rejected: process-wide singletons and unbounded fan-out

Global HTTP clients would maximize reuse but make credential lifetime and shutdown ownership
ambiguous. Unbounded `gather` minimizes ideal-case latency but amplifies provider throttling and
memory use. Both conflict with the repository's per-run isolation model.

### Rejected: large framework or state-management replacement

Replacing React contexts, the integration registry, or the persistence layer could address some
symptoms but would create a much larger compatibility surface than the measured problems require.

## Design

### Frontend state and delivery

`TabsProvider.updateDocument` becomes a stable callback and returns the existing state object for
an unchanged document. `Workbench` passes a stable canvas callback. Equality takes fast reference
paths before any deep serialization. Non-default pages load with `React.lazy` and `Suspense`.
Hashed assets receive `public, max-age=31536000, immutable`; the SPA entry remains `no-cache`.
FastAPI gzip middleware compresses eligible responses.

### Concurrency

`Settings.llm_max_concurrency` defaults to 8 and is constrained to 1..64. A semaphore created with
the persona graph bounds only model invocations, not deterministic scoring. Strategy backtests use
a separate `data_concurrency` argument defaulting to 8 and preserve request-symbol order.

### HTTP ownership

Network integration clients lazily create one `httpx.AsyncClient`, reuse it for the lifetime of the
run, and implement `aclose`. `RunContext` records only clients it owns and closes each unique owned
client. `RunManager` closes the context in a `finally` block after graph streaming. Injected test
transports remain supported.

### Persistence

Storage exposes a targeted `thread_expired(thread_id, cutoff)` query used by retention after locks
are acquired. `delete_thread` keeps its transactional age check. `add_event` accepts an optional
known `thread_id`; RunManager supplies it and uses the existing atomic status-with-event operation
when a run starts.

### Packaging

The `app` extra no longer includes TensorFlow. The existing `ml` extra remains the opt-in route, so
`uv sync --extra app --extra ml` restores LSTM support. Documentation and container guidance state
that distinction.

## Verification

- Focused red/green tests for editor state, persona concurrency, backtest concurrency, context
  closure, targeted retention, event query count, static headers, and gzip.
- Web unit tests, TypeScript check, and production build with multiple JS chunks.
- Ruff including `PERF`/`ASYNC`, strict mypy, package tests, and the full application suite.
- `uv lock --check` after optional-dependency changes.
