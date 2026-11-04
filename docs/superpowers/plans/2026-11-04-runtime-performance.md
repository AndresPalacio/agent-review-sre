# Runtime Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the identified runtime, packaging, persistence, and frontend inefficiencies without changing public behavior.

**Architecture:** Apply bounded concurrency and explicit resource ownership at current subsystem boundaries. Keep persistence and UI contracts stable, then reduce shipped work through optional ML packaging and lazy browser chunks.

**Tech Stack:** Python 3.12, asyncio, httpx, SQLAlchemy, FastAPI/Starlette, React 19, TypeScript, Vite, pytest, Vitest.

**Spec:** `docs/superpowers/specs/2026-11-04-runtime-performance-design.md`

## Global Constraints

- Preserve all public HTTP/Python contracts and financial results.
- Preserve input/result ordering under concurrency.
- Default LLM concurrency is 8, configurable from 1 through 64.
- Default backtest data concurrency is 8 and must never be unbounded.
- Close only integration clients owned by a `RunContext`.
- Recheck thread expiry after locks and before deleting checkpoints.
- Keep LSTM available through the existing `ml` extra.

## Review Focus

- A context containing the same owned client under multiple integration IDs closes it once.
- Cancellation during graph streaming still closes the run context.
- A provider failure during concurrent history loading cancels siblings and preserves the original error.
- A tab switch does not mark an unchanged flow dirty or emit an update loop.
- Static cache policy never makes `index.html` immutable.

---

### Task 1: Stable flow-editor updates

**Files:**
- Modify: `web/src/contexts/TabsContext.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/components/flow/FlowCanvas.tsx`
- Test: `web/src/components/flows/FlowLibrary.test.tsx`
- Test: `web/src/components/flow/FlowCanvas.test.tsx`

**Interfaces:**
- Consumes: existing `TabsState.updateDocument` and `FlowCanvasProps.onChange`.
- Produces: stable `updateDocument`; unchanged updates preserve the `tabs` array identity.

- [ ] Write a provider-level test that records renders and proves an identical document update does not republish context state.
- [ ] Run the focused Vitest files and verify the new test fails because state identity changes.
- [ ] Make tab actions stable with `useCallback`, return `current` for identical documents, add reference fast paths, and pass a stable canvas callback.
- [ ] Add a FlowCanvas regression test that rerenders with a new callback and proves unchanged nodes do not emit again.
- [ ] Run focused tests and then `npm test -- --run`.
- [ ] Commit as `perf(web): stabilize flow editor updates`.

### Task 2: Bounded analysis and backtest concurrency

**Files:**
- Modify: `src/fin_graph/config/settings.py`
- Modify: `src/fin_graph/agents/analysis/persona_team.py`
- Modify: `src/fin_graph/backtesting/strategies.py`
- Test: `tests/agents/analysis/test_persona_team.py`
- Test: `tests/backtesting/test_strategies.py`
- Modify: `.env.example`

**Interfaces:**
- Produces: `Settings.llm_max_concurrency: int`; `run_strategy_backtest(..., data_concurrency: int = 8)`.
- Preserves: persona finding order and strategy output order.

- [ ] Add a persona test with a blocking fake model proving no more than two invocations overlap when configured to two.
- [ ] Run it and verify unbounded `gather` fails the overlap assertion.
- [ ] Add the graph-level semaphore and validated setting.
- [ ] Add a strategy test proving multiple price loads overlap while never exceeding a supplied bound and results remain ordered.
- [ ] Run it and verify the sequential implementation fails the overlap assertion.
- [ ] Implement bounded `TaskGroup` history loading and validate positive concurrency.
- [ ] Run both focused files and the full Python suite for the task.
- [ ] Commit as `perf: bound model and market-data concurrency`.

### Task 3: Run-owned HTTP connection reuse

**Files:**
- Modify: `src/fin_graph/integrations/http.py`
- Modify: `src/fin_graph/integrations/yahoo/session.py`
- Modify: `src/fin_graph/integrations/context.py`
- Modify: `src/fin_graph/integrations/registry.py`
- Modify: `src/fin_graph/server/manager.py`
- Test: `tests/integrations/test_http.py`
- Test: `tests/integrations/yahoo/test_yahoo.py`
- Test: `tests/integrations/test_context.py`
- Test: `tests/server/test_manager.py`

**Interfaces:**
- Produces: `HttpJsonClient.aclose()`, `YahooSession.aclose()`, `RunContext.aclose()` and owned-client metadata.
- Consumes: `IntegrationRegistry.run_context()` and `RunManager.context_factory`.

- [ ] Add tests proving sequential calls reuse one transport/client lifecycle and `aclose` prevents further use.
- [ ] Run them and verify the current per-request clients fail reuse assertions.
- [ ] Implement lazy reusable clients with idempotent close.
- [ ] Add a context test proving duplicate owned references close once and borrowed clients stay open.
- [ ] Run it red, implement `RunContext` ownership, and have the registry mark built clients as owned.
- [ ] Add a manager cancellation/failure test proving context closure in `finally`.
- [ ] Run it red, update manager lifecycle, then run all focused integration/server tests.
- [ ] Commit as `perf: reuse run-scoped HTTP clients`.

### Task 4: Targeted retention and lean event writes

**Files:**
- Modify: `src/fin_graph/server/storage.py`
- Modify: `src/fin_graph/server/app.py`
- Modify: `src/fin_graph/server/manager.py`
- Test: `tests/server/test_storage.py`
- Test: `tests/server/test_api.py`
- Test: `tests/server/test_manager.py`

**Interfaces:**
- Produces: `Storage.thread_expired(thread_id, cutoff) -> bool`; optional `thread_id` on `add_event`.
- Preserves: event ordering, notifications, and atomic terminal status events.

- [ ] Add a retention-cycle test proving the global expiry query runs once for multiple expired threads and each thread receives a targeted recheck.
- [ ] Run it red, implement `thread_expired`, and replace the inner global scan.
- [ ] Add a SQL statement-count test proving known-thread event insertion executes no run lookup.
- [ ] Run it red, add the optional parameter, and pass it from RunManager.
- [ ] Replace the two-step running status transition with `set_status_with_event` and run manager/storage suites.
- [ ] Commit as `perf(server): target retention and event writes`.

### Task 5: Slim application packaging

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `README.md`
- Modify: `docs/operations/local-app.md`
- Modify: `Dockerfile`

**Interfaces:**
- Produces: default `app` extra without TensorFlow; opt-in `app` plus `ml` installation.
- Preserves: the `ml` extra and lazy LSTM dependency error behavior.

- [ ] Remove TensorFlow from `app` while retaining it in `ml`; refresh the lock file.
- [ ] Document default and ML-enabled install/container commands.
- [ ] Run `uv lock --check`, build the wheel, and verify `uv export --extra app --no-dev` excludes TensorFlow while `--extra app --extra ml` includes it.
- [ ] Commit as `perf(packaging): make ML runtime optional`.

### Task 6: Lazy frontend and efficient static delivery

**Files:**
- Modify: `web/src/App.tsx`
- Modify: `src/fin_graph/server/app.py`
- Test: `tests/server/test_api.py`
- Test: `web/src/App.test.tsx`

**Interfaces:**
- Produces: lazy non-default screens; immutable caching for `/assets/*`; gzip for eligible responses.
- Preserves: no-cache SPA entry and all route content.

- [ ] Add server tests for immutable hashed assets, no-cache index fallback, and gzip negotiation.
- [ ] Run them red, add gzip middleware and path-specific headers, then run them green.
- [ ] Convert backtests, native views, and settings to `React.lazy` with a small accessible fallback.
- [ ] Run web tests, typecheck, and production build; verify more than one JS chunk is emitted.
- [ ] Commit as `perf(web): lazy load screens and cache assets`.

### Task 7: PERF cleanup and release verification

**Files:**
- Modify: `src/fin_graph/optimization/harness/artifacts.py`
- Modify: `src/fin_graph/server/storage.py`
- Modify: `src/fin_graph/workflows/analysis/graph.py`

**Interfaces:**
- Preserves behavior; produces a clean Ruff `PERF,ASYNC` run.

- [ ] Use the existing failing Ruff run as RED and replace append loops with equivalent comprehensions/extensions.
- [ ] Run Ruff, formatting, mypy, import-linter, TypeScript, web build/tests, workflow tests, and the full Python suite.
- [ ] Review the whole diff against the spec and record any rulings or deferred minor findings.
- [ ] Commit as `perf: clear remaining performance lint`.
