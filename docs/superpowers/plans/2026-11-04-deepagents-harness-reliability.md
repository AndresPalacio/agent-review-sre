# Deep Agents Harness Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make full Deep Agents optimization fail fast and correctly handle the observed deep-value decision and bilingual evaluation failures.

**Architecture:** Add a bounded capability probe at the harness boundary, retain the official Deep Agent factory/backend, and make the smallest semantic changes at portfolio and evaluation boundaries.

**Tech Stack:** Python 3.12, Deep Agents, LangChain, Ollama, Pydantic, better-harness, pytest.

**Spec:** `docs/superpowers/specs/2026-11-04-deepagents-harness-reliability-design.md`

## Global Constraints

- Keep `create_deep_agent` and its filesystem backend.
- Never expose holdout or scorecard case data to the proposer.
- Never turn a repeated or failed `hold` into an automatic trade.
- Preflight must complete in a bounded child process.
- Preserve existing scripted smoke behavior.

## Review Focus

- Preflight executes before any baseline case and is skipped for injected proposers.
- A printed function JSON block cannot be misclassified as an intentional no-op.
- Scratch preflight files cannot escape their bounded workspace.
- Portfolio retry happens only for verified theses with a directional action.
- The real fixture, rather than a copied answer object, accepts Spanish terminology.

### Task 1: Guard and preflight the Deep Agent proposer

**Files:**
- Modify: `src/fin_graph/optimization/harness/proposer.py`
- Modify: `src/fin_graph/optimization/harness/experiment.py`
- Modify: `tests/optimization/harness/test_optimization.py`
- Modify: `tests/optimization/harness/test_experiment.py`

**Interfaces:**
- Produces: `ToolProtocolError`, `preflight_proposer(config, layout)`.
- Consumes: existing `make_optimizer_agent`, `HarnessConfig`, and `RunLayout`.

- [ ] Add tests for printed tool JSON, real scratch edit behavior, preflight-before-evaluation, and injected-proposer bypass.
- [ ] Run focused tests and confirm the missing behavior fails.
- [ ] Implement staged startup, serialized-call detection, bounded subprocess preflight, and blocked summary handling.
- [ ] Run focused tests green.

### Task 2: Retry contradictory portfolio holds safely

**Files:**
- Modify: `src/fin_graph/workflows/analysis/prompts.py`
- Modify: `src/fin_graph/workflows/analysis/services.py`
- Modify: `src/fin_graph/workflows/analysis/services.py`
- Modify: `tests/workflows/analysis/test_services.py`

**Interfaces:**
- Preserves: `AnalysisServices.decide(...) -> PortfolioDecision`.
- Produces: at most one second structured call when the first answer is an unexplained hold.

- [ ] Add one test where hold then buy returns buy, and one where hold then hold remains hold.
- [ ] Run focused tests red.
- [ ] Clarify the general action policy and implement the bounded retry.
- [ ] Run focused tests green.

### Task 3: Align Spanish narrative and evaluation data

**Files:**
- Modify: `src/fin_graph/workflows/analysis/prompts.py`
- Modify: `src/fin_graph/evals/data/equities/001-deep-value/answer.yml`
- Modify: `tests/evals/market/test_equity_evals.py`

**Interfaces:**
- Preserves: pipe-separated keyword alternatives in the scorer.

- [ ] Add a test using the loaded fixture and Spanish deep-value wording; verify it fails one keyword.
- [ ] Add the bilingual alias, evidence-conditional narrative wording, and restore the category
  phrase only from a supporting deterministic valuation score.
- [ ] Run eval tests green and regenerate/check synthetic fixtures if required.

### Task 4: Validate the integrated change

- [ ] Run focused optimization, workflow, and eval suites.
- [ ] Run Ruff and the full Python test suite.
- [ ] Run `harness validate` and a fresh offline `harness smoke`.
- [ ] Run the real local proposer preflight and record whether the configured 3B model is capable.
- [ ] Review the whole diff against the spec and report any remaining model limitation.
