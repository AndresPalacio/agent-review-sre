# AI Hedge Fund Compatibility Surfaces Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make existing AI Hedge Fund HTTP/SSE, CLI, startup, and Python consumers work unchanged against Fin Graph.

**Architecture:** A dedicated FastAPI router translates frozen legacy schemas into the v1/v2 application services and adapts the common event bus to exact SSE dialects. Thin CLI and import shims delegate to the same services; SQLite powers the one-command local profile and Postgres remains the distributed profile.

**Tech Stack:** FastAPI, Pydantic 2, SQLAlchemy async, SSE, Typer, Deep Agents/LangGraph services from Plans 02/03, pytest/httpx.

**Spec:** `docs/superpowers/specs/2026-11-03-ai-hedge-fund-drop-in-replacement-design.md`

**Prerequisites:** Plans 01, 02, and 03.

## Global Constraints

- Preserve exact legacy paths, methods, status codes, response keys, error bodies, content types, and SSE event/wire order.
- `/v1/*` native routes and their role enforcement continue to work.
- Legacy unauthenticated routes are seamless on loopback; non-loopback requires authentication unless `ALLOW_INSECURE_LEGACY_API=true` is explicit.
- Provider keys are encrypted at rest and redacted everywhere.
- Shims contain translation only; all finance and backtest logic remains in native services.

## Review Focus

- An original frontend request with no auth on loopback must work while the same request remotely fails closed; Task 2 tests it.
- Client disconnect or cancellation during SSE must not leak a background run; Tasks 4 and 5 test it.
- Duplicate flow/run/job mutations must be idempotent or return the exact legacy conflict; Tasks 3 and 5 test it.
- URL-encoded model, provider, skill, and resource names must not enable traversal or ambiguous routing; Tasks 2 and 5 test it.
- A clean installed wheel must satisfy legacy imports without depending on the source checkout; Task 6 tests it.

---

### Task 1: Freeze and implement the public compatibility schemas

**Files:**
- Create: `src/fin_graph/compat/ai_hedge_fund/schemas.py`
- Create: `src/fin_graph/compat/ai_hedge_fund/adapters.py`
- Create: `tests/compat/http/test_schemas.py`
- Add fixtures: `tests/compat/fixtures/http/*.json`

**Interfaces:**
- Produces all request/response/event Pydantic models required by the frozen OpenAPI and route tests, plus `to_native_*`/`from_native_*` adapters.
- Models preserve aliases, omission/null behavior, enum strings, datetime formatting, list order, and extra-field policy from the reference.

- [ ] **Step 1: Record OpenAPI/schema examples and write failing round-trip tests**

Assert every route model validates recorded good requests, rejects recorded bad requests with matching locations/messages/status, serializes exact golden JSON, and round-trips native domain values without losing money/date precision.

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest tests/compat/http/test_schemas.py -q`

Expected: FAIL because schemas/adapters do not exist.

- [ ] **Step 3: Implement schemas and pure adapters**

Keep HTTP exceptions out of adapters; adapters raise typed compatibility errors mapped by routers.

- [ ] **Step 4: Run schema and domain tests**

Run: `uv run pytest tests/compat/http/test_schemas.py tests/domain tests/quant -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/fin_graph/compat/ai_hedge_fund/schemas.py src/fin_graph/compat/ai_hedge_fund/adapters.py tests/compat/http tests/compat/fixtures/http && git commit -m "feat: define ai hedge fund compatibility schemas"`

### Task 2: Add health, model, provider-key, Ollama, storage, and skill routers

**Files:**
- Create: `src/fin_graph/server/compat/__init__.py`
- Create: `src/fin_graph/server/compat/security.py`
- Create: `src/fin_graph/server/compat/basic.py`
- Create: `src/fin_graph/server/compat/models.py`
- Create: `src/fin_graph/server/compat/api_keys.py`
- Create: `src/fin_graph/server/compat/ollama.py`
- Create: `src/fin_graph/server/compat/skills.py`
- Modify: `src/fin_graph/server/app.py`
- Create: `tests/compat/http/test_basic_routes.py`
- Create: `tests/compat/http/test_settings_routes.py`

**Interfaces:**
- Produces a compatibility `APIRouter` with `/`, `/health`, `/ping`, `/language-models`, `/api-keys`, `/ollama`, `/storage/save-json`, and `/v2/skills` contracts.
- Security dependency receives the actual peer/proxy context and `CompatibilityProfile`; it never trusts forwarded headers outside configured proxy CIDRs.

- [ ] **Step 1: Write failing route and security contract tests**

Cover recorded success/errors, exact model catalog, encrypted key CRUD/deactivation/last-used, Ollama status/start/stop/pull/progress/delete with fake service, JSON filename confinement, skill Unicode/resource paths, loopback no-auth, remote auth, spoofed forwarding headers, and unsafe opt-in.

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest tests/compat/http/test_basic_routes.py tests/compat/http/test_settings_routes.py -q`

Expected: FAIL with 404/missing routers.

- [ ] **Step 3: Implement routers over compatibility store and existing Ollama/model/skill services**

Mount before the static catch-all. Storage writes only under the configured export directory and uses atomic replacement.

- [ ] **Step 4: Run compatibility and native server security tests**

Run: `uv run pytest tests/compat/http/test_basic_routes.py tests/compat/http/test_settings_routes.py tests/server/test_security.py tests/server/test_http_guards.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/fin_graph/server/compat src/fin_graph/server/app.py tests/compat/http && git commit -m "feat: add legacy settings and resource routes"`

### Task 3: Add flow CRUD, revisions, runs, and graph compilation

**Files:**
- Create: `src/fin_graph/flows/__init__.py`
- Create: `src/fin_graph/flows/models.py`
- Create: `src/fin_graph/flows/compiler.py`
- Create: `src/fin_graph/flows/service.py`
- Create: `src/fin_graph/server/compat/flows.py`
- Create: `tests/flows/test_compiler.py`
- Create: `tests/compat/http/test_flow_routes.py`

**Interfaces:**
- Produces `FlowGraph`, `FlowNode`, `FlowEdge`, `compile_flow(graph: FlowGraph, services: FlowServices) -> CompiledStateGraph`, and `FlowService.create/get/list/update/delete/duplicate/start_run/get_run/list_cycles/cancel`.
- The router exposes exact `/flows` and `/flows/{flow_id}/runs` contracts.

- [ ] **Step 1: Write failing compiler and route contract tests**

Assert valid analyst fan-out/join/output compilation, cycle/orphan/duplicate/unknown node rejection, parameter/model propagation, immutable revision pinning, CRUD/duplicate semantics, list ordering/pagination, run/cycle history, idempotent cancellation, and restart persistence.

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest tests/flows/test_compiler.py tests/compat/http/test_flow_routes.py -q`

Expected: FAIL on missing flow modules/routes.

- [ ] **Step 3: Implement the typed compiler and service**

Compile only allowlisted node kinds; no `eval`, arbitrary import, filesystem path, or shell execution. Bind a run to its exact `FlowRevision` before scheduling.

- [ ] **Step 4: Run flow, storage, and analysis tests**

Run: `uv run pytest tests/flows tests/compat/http/test_flow_routes.py tests/compat/storage tests/graphs/analysis -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/fin_graph/flows src/fin_graph/server/compat/flows.py tests/flows tests/compat/http/test_flow_routes.py && git commit -m "feat: compile and persist compatible flows"`

### Task 4: Add hedge-fund run and v1 backtest SSE routes

**Files:**
- Create: `src/fin_graph/server/compat/events.py`
- Create: `src/fin_graph/server/compat/hedge_fund.py`
- Create: `tests/compat/http/test_hedge_fund_routes.py`
- Create: `tests/compat/http/test_v1_sse.py`
- Create: `tests/compat/http/test_api_rate_limiting.py`

**Interfaces:**
- Produces `legacy_sse(source: AsyncIterator[InternalEvent], dialect: LegacyDialect) -> AsyncIterator[bytes]` and routes under `/hedge-fund` for run, backtest, and agents.
- Uses Plan 02's persona team/portfolio/backtest services and returns Plan 04 Task 1 schemas.

- [ ] **Step 1: Write failing HTTP and byte-level SSE transcript tests**

Assert agents catalog, run/backtest requests, exact event names/data framing/order, analyst progress, portfolio result, error event, malformed input status, child failure, disconnect cancellation, one terminal event, and the reference-compatible rate-limit status/body/headers against recorded transcripts.

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest tests/compat/http/test_hedge_fund_routes.py tests/compat/http/test_v1_sse.py tests/compat/http/test_api_rate_limiting.py -q`

Expected: FAIL with 404/missing adapter.

- [ ] **Step 3: Implement event adaptation and routers**

Use Starlette streaming cancellation, bounded queues, the common event sink, and the existing bounded rate-limiter primitive. Never include raw prompts, provider keys, or tool payloads in progress.

- [ ] **Step 4: Run compatibility and native streaming tests**

Run: `uv run pytest tests/compat/http/test_hedge_fund_routes.py tests/compat/http/test_v1_sse.py tests/compat/http/test_api_rate_limiting.py tests/server/test_terminal_events.py tests/server/test_runtime_bounds.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/fin_graph/server/compat/events.py src/fin_graph/server/compat/hedge_fund.py tests/compat/http && git commit -m "feat: expose compatible hedge fund streams"`

### Task 5: Add v2 signal, risk, algorithm, and backtest routes

**Files:**
- Create: `src/fin_graph/server/compat/v2_signals.py`
- Create: `src/fin_graph/server/compat/v2_risk.py`
- Create: `src/fin_graph/server/compat/algorithms.py`
- Create: `src/fin_graph/server/compat/backtests.py`
- Create: `tests/compat/http/test_v2_routes.py`
- Create: `tests/compat/http/test_algorithm_jobs.py`

**Interfaces:**
- Exposes exact `/v2/{model}/predict|train`, `/v2/risk/...`, `/v2/algorithms`, and `/v2/backtests` contracts, including signal pagination and SSE/cancel.
- Resolves factories exclusively through Plan 03 registries and job manager.

- [ ] **Step 1: Write failing route, pagination, job, and dependency-error tests**

Cover all reference-visible models, trainable/view-only route availability, bad model/parameters/dates, missing extras, catalog/specification, start/get/signals/events/cancel, stable cursors, duplicate requests, restart, remote auth, and encoded path values.

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run --extra quant pytest tests/compat/http/test_v2_routes.py tests/compat/http/test_algorithm_jobs.py -q`

Expected: FAIL on missing routes.

- [ ] **Step 3: Implement thin registry/service routers**

Map typed domain errors to recorded status/body. Do not train or backtest in request handlers; schedule bounded jobs.

- [ ] **Step 4: Run all compatibility HTTP tests**

Run: `uv run --extra quant pytest tests/compat/http tests/server -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/fin_graph/server/compat tests/compat/http && git commit -m "feat: expose compatible v2 quantitative API"`

### Task 6: Add CLI and Python import shims

**Files:**
- Create: `src/fin_graph/cli/compat.py`
- Modify: `src/fin_graph/cli/main.py`
- Create: `src/src/__init__.py`
- Create: `src/src/main.py`
- Create: `src/src/backtester.py`
- Create: `src/src/agents/`
- Create: `src/src/backtesting/`
- Create: `src/src/data/`
- Create: `src/src/graph/`
- Create: `src/src/llm/`
- Create: `src/src/tools/`
- Create: `src/src/utils/`
- Create: `src/v2/`
- Create: `src/app/__init__.py`
- Create: `src/app/backend/__init__.py`
- Create: `src/app/backend/main.py`
- Modify: `pyproject.toml`
- Create: `tests/compat/test_cli.py`
- Create: `tests/compat/test_import_shims.py`
- Create: `tests/compat/test_wheel.py`

**Interfaces:**
- Adds native `fin-graph run`, `backtest`, `models`, `algorithms`, `event-study`, and `app` commands.
- Exposes documented legacy entrypoints/import signatures from `src.main`, `src.backtesting`, `v2.*`, and `app.backend.main:app`; every shim delegates to `fin_graph`.

- [ ] **Step 1: Write failing CLI, signature, and clean-wheel tests**

Assert legacy arguments/aliases/help/exit codes/JSON, native commands, every documented import, callable signatures, ASGI app identity, absence of financial logic in shim AST, and imports from an installed wheel outside the checkout.

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest tests/compat/test_cli.py tests/compat/test_import_shims.py tests/compat/test_wheel.py -q`

Expected: FAIL on missing commands/packages.

- [ ] **Step 3: Implement entrypoints, shims, and hatch package inclusion**

Package `src/fin_graph`, `src/src`, `src/v2`, and `src/app`. Keep compatibility warnings off normal stdout so JSON output remains exact.

- [ ] **Step 4: Build and test the wheel**

Run: `uv build --wheel`

Run: `uv run pytest tests/compat/test_cli.py tests/compat/test_import_shims.py tests/compat/test_wheel.py tests/cli -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/fin_graph/cli src/src src/v2 src/app pyproject.toml tests/compat/test_cli.py tests/compat/test_import_shims.py tests/compat/test_wheel.py && git commit -m "feat: add legacy cli and python shims"`

### Task 7: Add one-command local app and Postgres compatibility store

**Files:**
- Create: `src/fin_graph/compat/storage/postgres.py`
- Create: `src/fin_graph/server/factory.py`
- Create: `src/fin_graph/server/migrations/versions/0013_compatibility_store.py`
- Modify: `src/fin_graph/server/asgi.py`
- Modify: `src/fin_graph/cli/compat.py`
- Create: `app/run.bat`
- Create: `app/run.sh`
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `.gitignore`
- Create: `tests/compat/storage/test_postgres.py`
- Create: `tests/compat/test_local_app.py`

**Interfaces:**
- Produces `open_compat_postgres(pool) -> AsyncContextManager[CompatibilityStore]` and `create_app(runtime: RuntimeProfile) -> FastAPI` where profile selects SQLite/local or Postgres/distributed capabilities.
- `fin-graph app` initializes a clean local data directory and serves backend/static UI at the expected addresses; scripts delegate to that command.

- [ ] **Step 1: Write failing local startup and backend parity tests**

Assert clean SQLite startup without Postgres/admin key, health/readiness, loopback policy, restart persistence, bound-port error, graceful shutdown, Postgres protocol conformance, CAS concurrency, and Docker profile configuration.

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest tests/compat/test_local_app.py -q`

Run with test Postgres: `uv run pytest tests/compat/storage/test_postgres.py -m postgres -q`

Expected: FAIL on missing factory/backends.

- [ ] **Step 3: Implement runtime factory, Postgres store, scripts, and images**

Local mode uses in-process locks and reports distributed features unavailable. Postgres uses migrations/advisory locking and must satisfy the same store contract.

- [ ] **Step 4: Run local, Postgres when configured, and native server tests**

Run: `uv run pytest tests/compat/test_local_app.py tests/server -q`

Run when configured: `uv run pytest tests/compat/storage/test_postgres.py tests/server/test_postgres.py -m postgres -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add .gitignore src/fin_graph/compat/storage/postgres.py src/fin_graph/server src/fin_graph/cli/compat.py app Dockerfile docker-compose.yml tests/compat && git commit -m "feat: add local compatible app runtime"`

## Phase Gate

Run: `uv run --extra app ruff check src tests/compat`

Run: `uv run --extra app mypy`

Run: `uv run --extra app pytest tests/compat tests/server tests/cli -q`

Run: `uv build --wheel`

Expected: all pass; the frozen reference OpenAPI and SSE contract manifest has no unimplemented route.
