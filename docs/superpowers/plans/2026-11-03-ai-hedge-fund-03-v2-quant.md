# AI Hedge Fund V2 Quantitative Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port every implemented v2 data, signal, portfolio, risk, event-study, strategy, and backtest capability into the Fin Graph core.

**Architecture:** Alpha models and strategies share immutable point-in-time data contracts and deterministic registries. Heavy numerical dependencies are optional extras; application services expose async jobs and artifacts without coupling algorithms to HTTP or persistence.

**Tech Stack:** Python 3.12, Pydantic 2, NumPy, Pandas, SciPy, scikit-learn, statsmodels, joblib, optional TensorFlow, pytest.

**Spec:** `docs/superpowers/specs/2026-11-03-ai-hedge-fund-drop-in-replacement-design.md`

**Prerequisites:** Plans 01 and 02.

## Global Constraints

- Preserve v2 point-in-time and fail-loud behavior; only genuine absence of data yields no signal.
- Every stochastic model accepts/records a seed; artifacts record algorithm version, features, date range, and dependency versions.
- Base `fin-graph` imports successfully without `quant` or `ml` extras.
- Public model keys and strategy keys match the frozen reference registries.
- LLM alpha models use the existing provider factory and structured-call fallback; deterministic components remain authoritative.

## Review Focus

- Split/dividend-adjusted close must be preferred without breaking providers that omit it; Task 1 tests it.
- Importing the base package without heavy extras must not fail; Tasks 2 and 3 test lazy imports.
- Training on insufficient/non-finite data must fail explicitly and leave no partial artifact; Task 3 tests it.
- Monte Carlo and ML results must be repeatable for a fixed seed; Tasks 3 and 4 test it.
- Cancelled backtest jobs must emit one terminal event and never publish partial success; Task 6 tests it.

---

### Task 1: Unify v2 point-in-time data and cache contracts

**Files:**
- Modify: `src/fin_graph/domain/market.py`
- Create: `src/fin_graph/quant/__init__.py`
- Create: `src/fin_graph/quant/data.py`
- Create: `src/fin_graph/quant/cache.py`
- Create: `src/fin_graph/quant/features.py`
- Modify: `src/fin_graph/integrations/yahoo/client.py`
- Create: `tests/quant/test_data_contract.py`
- Create: `tests/quant/test_cache.py`

**Interfaces:**
- `Price` gains `adj_close: float | None = None`.
- Produces `MarketDataClient` protocol equivalent to v2 `DataClient`; `PointInTimeData(client, as_of)`; `FundamentalSnapshot`/`TechnicalSnapshot` builders; and `CachedMarketData(client, cache, namespace)` with canonical method/argument keys and atomic JSON writes.

- [ ] **Step 1: Write failing adjusted-price, date-boundary, cache, and error tests**

Assert adjusted-close preference, raw-close fallback, filing-date filtering, no future bars, fundamental/technical snapshot parity, stable cache key independent of keyword order, corrupted-cache failure, and infrastructure errors not cached as empty results.

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest tests/quant/test_data_contract.py tests/quant/test_cache.py -q`

Expected: FAIL on missing data/cache contracts and `adj_close`.

- [ ] **Step 3: Implement adapters and populate Yahoo adjusted close**

Reuse current clients; do not create another HTTP stack. Cache serialization must use the Pydantic model schema version.

- [ ] **Step 4: Run data, Yahoo, and market tests**

Run: `uv run pytest tests/quant/test_data_contract.py tests/quant/test_cache.py tests/integrations/yahoo tests/domain/test_market.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/fin_graph/domain/market.py src/fin_graph/quant src/fin_graph/integrations/yahoo/client.py tests/quant tests/integrations/yahoo tests/domain/test_market.py && git commit -m "feat: add point in time quant data contracts"`

### Task 2: Port deterministic alpha models

**Files:**
- Create: `src/fin_graph/quant/signals/__init__.py`
- Create: `src/fin_graph/quant/signals/base.py`
- Create: `src/fin_graph/quant/signals/llm_agent.py`
- Create: `src/fin_graph/quant/signals/pead.py`
- Create: `src/fin_graph/quant/signals/candlestick.py`
- Create: `src/fin_graph/quant/signals/news_sentiment.py`
- Create: `src/fin_graph/quant/signals/buffett.py`
- Create: `src/fin_graph/quant/signals/monte_carlo.py`
- Create: `tests/quant/signals/test_deterministic_models.py`
- Add fixtures: `tests/compat/fixtures/v2/signals/deterministic/*.json`

**Interfaces:**
- Produces `Signal(value: float, reasoning: str, components: Mapping[str, float], metadata: Mapping[str, JsonValue])`, `AlphaModel.predict(ticker: str, as_of: date, data: MarketDataClient) -> Signal`, `LLMAlphaModel` for Buffett/persona analysis over the existing structured model client, and `alpha_model_registry() -> Mapping[str, AlphaModelFactory]`.
- `Signal.value` is finite and clamped to `[-1, 1]`; model keys match the reference.

- [ ] **Step 1: Record and write failing per-model parity tests**

Assert signal value, components, metadata, adjusted-price behavior, missing data, as-of boundaries, scripted LLM persona output/fallback, and fixed-seed Monte Carlo against recorded reference outputs.

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest tests/quant/signals/test_deterministic_models.py -q`

Expected: FAIL because signal modules are absent.

- [ ] **Step 3: Port the models and lazy registry**

Reuse Fin Graph quant helpers where formulas match; preserve all eighteen implemented candlestick patterns. Lazy module loading must allow base import without the quant extra.

- [ ] **Step 4: Run deterministic signal tests**

Run: `uv run --extra quant pytest tests/quant/signals/test_deterministic_models.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/fin_graph/quant/signals tests/quant/signals tests/compat/fixtures/v2/signals/deterministic && git commit -m "feat: port deterministic v2 alpha models"`

### Task 3: Port trainable and forecasting alpha models

**Files:**
- Create: `src/fin_graph/quant/artifacts.py`
- Create: `src/fin_graph/quant/signals/trainable.py`
- Create: `src/fin_graph/quant/signals/technical_ml.py`
- Create: `src/fin_graph/quant/signals/technical_scan.py`
- Create: `src/fin_graph/quant/signals/arima.py`
- Create: `src/fin_graph/quant/signals/lstm.py`
- Create: `tests/quant/signals/test_trainable_models.py`
- Create: `tests/quant/signals/test_forecasts.py`
- Add fixtures: `tests/compat/fixtures/v2/signals/trainable/*.json`

**Interfaces:**
- Produces `TrainingRequest(ticker, start_date, end_date, seed, parameters)`, `ModelArtifact`, `TrainableAlphaModel.train(request, data, artifact_store) -> ModelArtifact`, and `predict(..., artifact: ModelArtifact | None = None) -> Signal`.
- `ArtifactStore` uses atomic publish-after-validation; LSTM imports TensorFlow only inside its factory.

- [ ] **Step 1: Write failing training, forecast, seed, and optional-extra tests**

Cover technical ML/scan features and predictions, ARIMA forecast, recursive LSTM sequence/scale behavior, fixed seed, insufficient samples, NaN/inf rejection, corrupt/incompatible artifact, atomic failure, and base import without TensorFlow.

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run --extra quant pytest tests/quant/signals/test_trainable_models.py tests/quant/signals/test_forecasts.py -q`

Expected: FAIL on missing interfaces/models.

- [ ] **Step 3: Port trainable models and artifact metadata**

Use the corrected reference LSTM design: windowed scaled sequences, scaled target, recursive multi-step forecast. Preserve reference hyperparameter defaults and explicit dependency errors.

- [ ] **Step 4: Run quant and ML variants**

Run: `uv run --extra quant pytest tests/quant/signals/test_trainable_models.py tests/quant/signals/test_forecasts.py -q`

Run in the supported app container: `uv run --extra ml pytest tests/quant/signals/test_forecasts.py -q`

Expected: the quant suite and the containerized ML suite pass. If native Windows has no compatible TensorFlow wheel, the base and `quant` profiles still import cleanly and the native LSTM entrypoint returns the recorded typed dependency-unavailable error; the supported app container remains fully functional.

- [ ] **Step 5: Commit**

Run: `git add src/fin_graph/quant tests/quant/signals tests/compat/fixtures/v2/signals/trainable && git commit -m "feat: port trainable and forecasting alpha models"`

### Task 4: Port consensus, sizing, risk, and event studies

**Files:**
- Create: `src/fin_graph/quant/portfolio.py`
- Create: `src/fin_graph/quant/risk.py`
- Create: `src/fin_graph/quant/execution.py`
- Create: `src/fin_graph/quant/event_study/__init__.py`
- Create: `src/fin_graph/quant/event_study/models.py`
- Create: `src/fin_graph/quant/event_study/engine.py`
- Create: `src/fin_graph/quant/event_study/stats.py`
- Create: `src/fin_graph/quant/event_study/plot.py`
- Create: `tests/quant/test_portfolio.py`
- Create: `tests/quant/test_monte_carlo_risk.py`
- Create: `tests/quant/event_study/test_engine.py`

**Interfaces:**
- Produces `combine_signals(signals: Sequence[Signal], weights: Mapping[str, float] | None) -> Signal`, `size_from_signal(signal, portfolio, price, limits) -> ProposedOrder`, `V2PaperBroker` adapter over the current paper ledger with the reference JSON contract, `simulate_risk(request: RiskSimulationRequest) -> RiskSimulation`, `run_event_study(request: EventStudyRequest, data: MarketDataClient) -> EventStudyResult`, and `plot_event_study(result, output: Path) -> Path`.

- [ ] **Step 1: Write failing parity and statistical-boundary tests**

Assert reference consensus/sizing, zero/negative weights, missing signals, paper-broker buy/sell/short/cover persistence and idempotency, fixed-seed percentile risk, invalid covariance/data, event windows at dataset edges, benchmark alignment, abnormal returns and CAR statistics.

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run --extra quant pytest tests/quant/test_portfolio.py tests/quant/test_monte_carlo_risk.py tests/quant/event_study -q`

Expected: FAIL on missing services.

- [ ] **Step 3: Port services over shared market and portfolio models**

Use finite-value validation at every public boundary and record seed/method in results.

- [ ] **Step 4: Run focused and current risk tests**

Run: `uv run --extra quant pytest tests/quant tests/domain/quant/test_risk.py tests/domain/test_orders.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/fin_graph/quant/portfolio.py src/fin_graph/quant/risk.py src/fin_graph/quant/event_study tests/quant && git commit -m "feat: add v2 portfolio risk and event studies"`

### Task 5: Port the algorithm strategy catalog

**Files:**
- Create: `src/fin_graph/strategies/__init__.py`
- Create: `src/fin_graph/strategies/base.py`
- Create: `src/fin_graph/strategies/sma_crossover.py`
- Create: `src/fin_graph/strategies/rsi_mean_reversion.py`
- Create: `src/fin_graph/strategies/momentum.py`
- Create: `src/fin_graph/strategies/catalog.py`
- Create: `tests/strategies/test_catalog.py`
- Create: `tests/strategies/test_algorithms.py`

**Interfaces:**
- Produces `AlgorithmSignal(timestamp, ticker, action, strength, metadata)`, `AlgorithmStrategy.generate(data: Sequence[Price], parameters: Mapping[str, JsonValue]) -> Sequence[AlgorithmSignal]`, `AlgorithmSpecification`, and `strategy_catalog() -> Mapping[str, AlgorithmFactory]`.

- [ ] **Step 1: Write failing catalog, parameter, and golden-signal tests**

Assert the three exact keys/specifications/defaults, duplicate registration rejection, invalid windows/thresholds, no future bar use, stable signal order, and reference golden signals.

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run --extra quant pytest tests/strategies -q`

Expected: FAIL because strategies are missing.

- [ ] **Step 3: Port strategies and the explicit registry**

No import-side-effect registration; factories validate parameters before reading data.

- [ ] **Step 4: Run strategy tests**

Run: `uv run --extra quant pytest tests/strategies -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/fin_graph/strategies tests/strategies && git commit -m "feat: port algorithm strategy catalog"`

### Task 6: Add shared v2 backtests, jobs, and artifacts

**Files:**
- Create: `src/fin_graph/backtesting/quant.py`
- Create: `src/fin_graph/backtesting/strategies.py`
- Create: `src/fin_graph/backtesting/jobs.py`
- Create: `src/fin_graph/backtesting/artifacts.py`
- Create: `tests/backtesting/test_quant_models.py`
- Create: `tests/backtesting/test_strategies.py`
- Create: `tests/backtesting/test_jobs.py`

**Interfaces:**
- Produces `run_alpha_backtest(request, model, data, *, event_sink, cancellation)`, `run_strategy_backtest(request, strategy, data, *, event_sink, cancellation)`, and `BacktestJobManager.start/get/cancel/signals/events` with persisted `BacktestJobRecord` from Plan 01.

- [ ] **Step 1: Write failing engine, paging, cancellation, and restart tests**

Assert reference trades/metrics/artifacts for alpha and strategy fixtures, bounded worker concurrency, stable signal pagination, replayable ordered events, cancellation race handling, one terminal event, failed-job details, and restart recovery.

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run --extra quant pytest tests/backtesting/test_quant_models.py tests/backtesting/test_strategies.py tests/backtesting/test_jobs.py -q`

Expected: FAIL on missing v2 services.

- [ ] **Step 3: Implement services over the shared fill loop and compatibility store**

Jobs own background tasks; algorithms remain pure. Persist job transitions with CAS and publish artifacts only after successful completion.

- [ ] **Step 4: Run the v2 engine gate**

Run: `uv run --extra quant pytest tests/quant tests/strategies tests/backtesting -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/fin_graph/backtesting tests/backtesting && git commit -m "feat: add v2 backtests and durable jobs"`

### Task 7: Import and validate the complete finance skill catalog

**Files:**
- Create: `src/fin_graph/skills/loader.py`
- Create: `src/fin_graph/skills/models.py`
- Modify: `src/fin_graph/skills/catalog.py`
- Create: `src/fin_graph/skills/finance/` (only files approved by Plan 01's license inventory)
- Create: `tests/skills/test_catalog_parity.py`
- Create: `tests/skills/test_resource_security.py`
- Modify: `NOTICE`

**Interfaces:**
- Produces `SkillRecord(id, title, description, root, resources)`, `load_skill_catalog(root: Traversable) -> tuple[SkillRecord, ...]`, and `read_skill_resource(skill_id: str, resource_path: str, max_bytes: int) -> SkillResource`.
- Deep Agents consumes validated roots through the existing Skills middleware; loading does not execute scripts.

- [ ] **Step 1: Write failing catalog parity and traversal tests**

Assert every licensed reference skill id/resource is present, stable sorting, frontmatter validation, Unicode paths, unknown skill/resource errors, `..`/absolute/symlink escape rejection, size limit, MIME detection, and no script execution.

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest tests/skills -q`

Expected: FAIL on missing loader/assets.

- [ ] **Step 3: Copy approved assets and implement the loader**

Use `apply_patch` for vendored textual assets, preserving bytes and paths; compare each resulting file to the snapshot hash and include the Vibe-Trading notice.

- [ ] **Step 4: Run skills, license, and package tests**

Run: `uv run pytest tests/skills tests/compat/test_licensing.py tests/test_license.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/fin_graph/skills tests/skills NOTICE && git commit -m "feat: add licensed finance skill catalog"`

## Phase Gate

Run: `uv run --extra quant ruff check src/fin_graph/quant src/fin_graph/strategies src/fin_graph/backtesting tests/quant tests/strategies tests/backtesting`

Run: `uv run --extra quant mypy`

Run: `uv run --extra quant pytest tests/quant tests/strategies tests/backtesting tests/skills -q`

Expected: all pass; base import without extras also passes `uv run python -I -c "import fin_graph"` in the base environment.
