# AI Hedge Fund parity report

Verified on 2026-11-03 against the frozen local AI Hedge Fund snapshot
`42cd3a84c47ccd9ca2a891146a2d818b2ea8c7908162ef107975239b7148a71f`.

## Closure

| Measure | Result |
|---|---:|
| Manifest contracts | 39 |
| Verified | 39 |
| Approved exclusions | 0 |
| Pending or unknown | 0 |
| SQLite acceptance checks | 14/14 |
| PostgreSQL acceptance checks | 14/14 |
| Public runtime digest | `a9036a91c42b90abde555aec416592908ce6e3da95266d13410332cc3f983f9b` |

The checker validates that every contract owns a unique existing pytest node, uses the current
reference snapshot, records an allowed comparison rule, has an executable evidence command, and
tracks the SHA-256 digest of its owning test file. Any stale or prose-only claim fails closed.

## Verified behavior

- The nineteen DeepAgents analyst personas, typed failure behavior, risk limits, portfolio
  decisions, v1 long/short backtests, and cancellation match the recorded contracts.
- V2 point-in-time data, deterministic and trainable signals, forecasts, VaR, strategy catalog,
  algorithm backtests, durable artifacts, pagination, restart, and cancellation are covered.
- HTTP schemas, status codes, SSE ordering, models/providers, API keys, Ollama, flows, runs,
  cycles, CLI commands, and Python import shims retain their public compatibility surfaces.
- The frozen reference frontend contract and the replacement workbench run against the new
  backend. The production web build is included in the wheel and container image.
- SQLite and PostgreSQL produce the same deterministic financial digest. PostgreSQL alone reports
  distributed locks and multi-worker capability, as intended.
- Provider secrets remain encrypted/redacted; remote compatibility calls require native auth;
  trading remains paper-only and subject to the native risk/approval controls.
- The clean wheel imports outside the repository. Docker builds the web UI, installs the complete
  `app` profile, runs as `fingraph`, owns `/data`, and answers `/health`.
- The obsolete SRE product, datasets, routes, integrations, dependencies, tests, and UI were
  removed; only shared finance/runtime infrastructure remains.

## Fresh release gate

| Gate | Result |
|---|---:|
| Ruff / format / mypy / import-linter / lock | Pass |
| Main Python suite | 1,318 passed; 24 optional skips |
| Workflow package | 125 passed; 4 optional-extra skips |
| Web TypeScript | Pass |
| Vitest | 40 passed |
| Playwright | 2 passed |
| Offline equity evaluation | 16/16 passed; signal 1.0; category 1.0 |
| Scripted smoke evaluation | 3/3 passed; signal 1.0; category 1.0 |
| Ollama `llama3.2:3b` smoke | 3/3 passed; 22,556 tokens; 89.67 s mean; no fallback/error |
| Optional optimization config | Valid; 16 cases across train, holdout, and scorecard |
| Wheel build and clean-install tests | Pass; 5/5 |
| Docker non-root runtime and health | Pass |
| Manifest checker | 39/39 closed |

Optional skips are limited to unconfigured PostgreSQL/Ollama/harness extras in the general suite,
plus Windows symlink support. PostgreSQL acceptance itself was run separately against a temporary
pgvector-enabled PostgreSQL 17 instance and passed before it was removed.

## Reproduce

```powershell
uv run python scripts/check_compatibility_manifest.py
uv run pytest tests/compat tests/acceptance -q
uv run python scripts/acceptance_ai_hedge_fund.py --backend sqlite --output artifacts/acceptance
uv run fin-graph eval run --suite smoke --model scripted --output-dir .runs/scripted
uv run --extra harness fin-graph harness validate optimization/fin.toml
uv run fin-graph eval run --suite smoke --model ollama:llama3.2:3b --output-dir .runs/ollama
```

For PostgreSQL, set `FIN_GRAPH_TEST_DATABASE_URL` to an isolated pgvector-enabled database and run
the same acceptance script with `--backend postgres`. The JSON and Markdown reports contain no
database URL, bootstrap key, provider key, or raw credential.

## Deliberate boundary

The release gate proves deterministic finance behavior, transport compatibility, storage,
concurrency, packaging, and offline orchestration. The recorded Ollama smoke proves the three
selected scenarios completed with the local model; it does not claim byte-identical prose or
general availability/quality of third-party market-data and model providers. Live smokes cannot
override an offline contract failure.
