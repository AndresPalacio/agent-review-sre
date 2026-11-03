# AI Hedge Fund V1 Deep Agents Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reproduce the reference v1 analyst, portfolio, risk, and backtesting behavior on one Fin Graph core orchestrated by Deep Agents.

**Architecture:** Persona calculations are deterministic domain functions; Deep Agent subagents gather tools and narrate those calculations. A typed supervisor selects analysts and hands their normalized signals to deterministic risk and portfolio services.

**Tech Stack:** Python 3.12, Pydantic 2, Deep Agents 0.7, LangGraph 1.2, existing Fin Graph market/risk/ledger code, pytest.

**Spec:** `docs/superpowers/specs/2026-11-03-ai-hedge-fund-drop-in-replacement-design.md`

**Prerequisite:** `docs/superpowers/plans/2026-11-03-ai-hedge-fund-01-parity-foundation.md`

## Global Constraints

- Port behavior from the frozen local reference, not its private state or `.env`.
- The nineteen public analyst keys and their order must equal `src/utils/analysts.py` in the snapshot.
- LLM output may explain but cannot alter deterministic signal, confidence, risk limits, allowed actions, quantity, or paper-only status.
- Every historical read is filtered on or before `as_of`; infrastructure failure is not a neutral signal.
- No real-broker interface is introduced.

## Review Focus

- Selecting no analysts means all nineteen in reference order; Task 4 tests it.
- One failed analyst must remain visible while other selected analysts finish; Task 4 tests it.
- Missing fundamentals must create explicit data gaps, not zeros; Tasks 2 and 3 test it.
- Short-only and mixed portfolios must preserve cash/margin/P&L invariants; Task 5 tests them.
- A backtest date must never read a later filing or price bar; Task 6 tests it.

---

### Task 1: Define the canonical analyst catalog

**Files:**
- Create: `src/fin_graph/domain/analysts.py`
- Create: `tests/domain/test_analyst_catalog.py`
- Modify: `src/fin_graph/graphs/analysis/analysts.py`
- Modify: `tests/graphs/analysis/test_graph.py`

**Interfaces:**
- Produces: `AnalystId(StrEnum)` with nineteen legacy values; `AnalystDefinition(id, display_name, description, investing_style, domains, order, prompt_key, scorer_key)`; `analyst_catalog() -> tuple[AnalystDefinition, ...]`; `resolve_analysts(ids: Sequence[str] | None) -> tuple[AnalystDefinition, ...]`.
- Existing six domain analysts remain addressable through the canonical catalog; `Domain` continues to describe tool access rather than persona identity.

- [ ] **Step 1: Write failing catalog tests**

Assert all nineteen keys, display names and order match the recorded catalog; duplicates fail; `None` resolves all; an explicit list preserves caller order without duplicates; an unknown key raises a public validation error.

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest tests/domain/test_analyst_catalog.py -q`

Expected: FAIL because the catalog does not exist.

- [ ] **Step 3: Implement catalog types and adapt the six existing definitions**

Keep prompts in resources/modules referenced by `prompt_key`; do not put executable functions inside the frozen metadata record.

- [ ] **Step 4: Run catalog and analysis graph tests**

Run: `uv run pytest tests/domain/test_analyst_catalog.py tests/graphs/analysis -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/fin_graph/domain/analysts.py src/fin_graph/graphs/analysis/analysts.py tests/domain/test_analyst_catalog.py tests/graphs/analysis/test_graph.py && git commit -m "feat: define complete analyst catalog"`

### Task 2: Port value and quality persona scorers

**Files:**
- Create: `src/fin_graph/domain/company.py`
- Create: `src/fin_graph/domain/quant/personas/__init__.py`
- Create: `src/fin_graph/domain/quant/personas/damodaran.py`
- Create: `src/fin_graph/domain/quant/personas/ackman.py`
- Create: `src/fin_graph/domain/quant/personas/munger.py`
- Create: `src/fin_graph/domain/quant/personas/burry.py`
- Create: `src/fin_graph/domain/quant/personas/pabrai.py`
- Create: `tests/domain/quant/personas/test_value_quality.py`
- Add fixtures: `tests/compat/fixtures/v1/personas/value-quality/*.json`

**Interfaces:**
- Each module produces `score_<persona>(snapshot: CompanySnapshot) -> QuantScore` and component functions named after the recorded reference analyses.
- Produces immutable `CompanySnapshot(ticker, as_of, prices, metrics, line_items, news, insider_trades, market_cap)` in `domain/company.py`; it rejects observations dated after `as_of`. Every scorer output uses `QuantScore`.

- [ ] **Step 1: Record fixtures and write failing differential tests**

For AAPL/MSFT/TSLA plus missing-data and negative-value cases, assert signal, confidence, component scores, intrinsic values and data gaps against the reference oracle; also assert `CompanySnapshot` rejects future observations.

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest tests/domain/quant/personas/test_value_quality.py -q`

Expected: FAIL on missing persona modules.

- [ ] **Step 3: Port the five deterministic scorers**

Preserve reference formulas and rounding only at public serialization boundaries. Reuse `domain.quant.common`, `valuation`, `buffett`, and `graham` helpers instead of copying them.

- [ ] **Step 4: Run persona and existing quant tests**

Run: `uv run pytest tests/domain/quant/personas/test_value_quality.py tests/domain/quant -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/fin_graph/domain/quant/personas tests/domain/quant/personas tests/compat/fixtures/v1/personas/value-quality && git commit -m "feat: port value and quality analyst scorers"`

### Task 3: Port growth, macro, sentiment, and tail-risk persona scorers

**Files:**
- Create: `src/fin_graph/domain/quant/personas/wood.py`
- Create: `src/fin_graph/domain/quant/personas/lynch.py`
- Create: `src/fin_graph/domain/quant/personas/fisher.py`
- Create: `src/fin_graph/domain/quant/personas/jhunjhunwala.py`
- Create: `src/fin_graph/domain/quant/personas/druckenmiller.py`
- Create: `src/fin_graph/domain/quant/personas/taleb.py`
- Create: `src/fin_graph/domain/quant/personas/growth.py`
- Create: `src/fin_graph/domain/quant/personas/news.py`
- Create: `tests/domain/quant/personas/test_growth_macro.py`
- Add fixtures: `tests/compat/fixtures/v1/personas/growth-macro/*.json`

**Interfaces:**
- Produces `score_<persona>(snapshot: CompanySnapshot) -> QuantScore` for the remaining persona-specific analysts. Generic technical/fundamental/sentiment/valuation analysts delegate to existing scorers.

- [ ] **Step 1: Record fixtures and write failing differential tests**

Cover growth, contraction, high leverage, insider sales, positive/negative news, fat-tail price series, empty news, and insufficient price history. Assert every reference component and explicit data gap.

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest tests/domain/quant/personas/test_growth_macro.py -q`

Expected: FAIL on missing scorers.

- [ ] **Step 3: Port the deterministic scorers and register all scorer keys**

Keep statistical helpers pure and seed any stochastic calculation. Taleb tail-risk calculations must reject non-finite price data rather than silently coerce it.

- [ ] **Step 4: Run all quant parity tests**

Run: `uv run pytest tests/domain/quant tests/compat/test_contract_oracle.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/fin_graph/domain/quant/personas tests/domain/quant/personas tests/compat/fixtures/v1/personas/growth-macro && git commit -m "feat: port growth macro and sentiment scorers"`

### Task 4: Assemble the selectable Deep Agent analyst team

**Files:**
- Create: `src/fin_graph/graphs/analysis/personas.py`
- Create: `src/fin_graph/graphs/analysis/compat.py`
- Create: `src/fin_graph/graphs/analysis/persona_prompts.py`
- Modify: `src/fin_graph/domain/requests/model.py`
- Modify: `src/fin_graph/graphs/chat/graph.py`
- Create: `tests/graphs/analysis/test_persona_team.py`

**Interfaces:**
- `AnalysisRequest` gains `analysts: tuple[AnalystId, ...] | None` without changing existing JSON defaults.
- Produces `build_persona_subagents(model: BaseChatModel, definitions: Sequence[AnalystDefinition], tools: Sequence[BaseTool]) -> list[SubAgent]` and `make_compat_analysis_agent(model, registry, context, services, *, checkpointer=None) -> CompiledStateGraph`.
- Each subagent returns `PersonaFinding(analyst_id, ticker, score, reasoning, evidence_ids, error)`; the supervisor returns `LegacyAnalysisBundle` before public adaptation.

- [ ] **Step 1: Write failing orchestration tests with scripted models**

Assert explicit selection launches only those analysts, default launches nineteen in catalog order, tool domains are isolated, deterministic score wins over contradictory model text, one child failure is preserved, cancellation propagates, and no trade tool is available to an analyst.

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest tests/graphs/analysis/test_persona_team.py -q`

Expected: FAIL on missing persona team.

- [ ] **Step 3: Implement Deep Agent assembly and typed aggregation**

Use `create_deep_agent` and explicit subagent specs; reuse current masking, evidence, disclaimer, timeout, and model-call limits. Do not parse free-form text for signal/confidence.

- [ ] **Step 4: Run analysis and chat suites**

Run: `uv run pytest tests/graphs/analysis tests/graphs/chat tests/agents -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/fin_graph/graphs/analysis src/fin_graph/graphs/chat/graph.py src/fin_graph/domain/requests/model.py tests/graphs/analysis/test_persona_team.py && git commit -m "feat: orchestrate full analyst team with deep agents"`

### Task 5: Reproduce v1 risk and portfolio decisions

**Files:**
- Create: `src/fin_graph/compat/ai_hedge_fund/__init__.py`
- Create: `src/fin_graph/compat/ai_hedge_fund/models.py`
- Create: `src/fin_graph/compat/ai_hedge_fund/portfolio.py`
- Create: `tests/compat/v1/test_portfolio_parity.py`
- Add fixtures: `tests/compat/fixtures/v1/portfolio/*.json`

**Interfaces:**
- Produces `LegacyHedgeFundRequest`, `LegacyAnalystSignal`, `LegacyPortfolioDecision`, and `LegacyHedgeFundResult` matching recorded field names.
- Produces `build_legacy_portfolio_result(findings: Sequence[PersonaFinding], portfolio: Portfolio, prices: Mapping[str, float]) -> LegacyHedgeFundResult`.

- [ ] **Step 1: Write failing long/short/hold and invariant tests**

Assert exact reference action/quantity/confidence for golden inputs; test insufficient cash, insufficient shares, short margin, mixed positions, zero/negative prices, contradictory analyst votes, and empty findings.

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest tests/compat/v1/test_portfolio_parity.py -q`

Expected: FAIL on missing compatibility models/service.

- [ ] **Step 3: Implement adapters over existing risk, allowed-action, and fill rules**

Preserve legacy field names at the boundary while using current `Portfolio`, `PositionLimit`, `compute_allowed_actions`, and `clamp_decision` internally.

- [ ] **Step 4: Run portfolio, orders, fills, and compatibility tests**

Run: `uv run pytest tests/compat/v1/test_portfolio_parity.py tests/domain/test_orders.py tests/domain/quant/test_risk.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/fin_graph/compat/ai_hedge_fund tests/compat/v1 tests/compat/fixtures/v1/portfolio && git commit -m "feat: reproduce legacy portfolio decisions"`

### Task 6: Add the v1 backtest application service

**Files:**
- Create: `src/fin_graph/backtesting/__init__.py`
- Create: `src/fin_graph/backtesting/models.py`
- Create: `src/fin_graph/backtesting/engine.py`
- Create: `src/fin_graph/backtesting/metrics.py`
- Create: `src/fin_graph/backtesting/service.py`
- Create: `tests/backtesting/test_v1_parity.py`
- Create: `tests/backtesting/test_point_in_time.py`
- Add fixtures: `tests/compat/fixtures/v1/backtests/*.json`

**Interfaces:**
- Produces `BacktestRequest(tickers, start_date, end_date, initial_capital, margin_requirement, analysts, model)`, `BacktestSnapshot`, `BacktestMetrics`, `BacktestResult`, and `async run_v1_backtest(request, analysis_runner, data_client, *, event_sink, cancellation) -> BacktestResult`.
- The engine uses existing `apply_fill(..., partial=True)` and a data view capped at each simulated date.

- [ ] **Step 1: Write failing reference-parity and lookahead tests**

Cover long-only, short-only and long/short fixtures; assert portfolio values, trades, benchmark, Sharpe/max drawdown/win rate and terminal state. Add a future filing/bar that would change a decision and assert it is never observed.

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest tests/backtesting/test_v1_parity.py tests/backtesting/test_point_in_time.py -q`

Expected: FAIL because the backtesting service is absent.

- [ ] **Step 3: Implement the shared dated loop, metrics, and event sink**

The loop accepts an injected runner/data client/clock; no HTTP, CLI, or global cache imports. Emit deterministic progress and one terminal event.

- [ ] **Step 4: Run the full v1 core gate**

Run: `uv run pytest tests/backtesting tests/domain/quant tests/graphs/analysis tests/compat/v1 -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/fin_graph/backtesting tests/backtesting tests/compat/fixtures/v1/backtests && git commit -m "feat: add point in time v1 backtester"`

## Phase Gate

Run: `uv run ruff check src/fin_graph/domain src/fin_graph/graphs/analysis src/fin_graph/backtesting src/fin_graph/compat/ai_hedge_fund tests/domain tests/graphs/analysis tests/backtesting tests/compat/v1`

Run: `uv run mypy`

Run: `uv run pytest tests/domain tests/graphs/analysis tests/graphs/chat tests/backtesting tests/compat/v1 -q`

Expected: all pass and every v1 deterministic golden result matches the frozen reference.
