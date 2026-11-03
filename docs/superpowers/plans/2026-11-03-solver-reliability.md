# Solver reliability implementation plan

> **For agentic workers:** Use superpowers:executing-plans or superpowers:subagent-driven-development; preserve the existing uncommitted implementation.

**Goal:** Correct reproduced end-to-end failures and make paired evaluation more representative and honest.

**Architecture:** Retain native DeepAgents/LangChain routing, tools, summaries and repair jumps. Add only boundary validation, a bounded summary model, and offline evaluation improvements justified by reproductions.

**Tech Stack:** Python 3.12/3.14, Pydantic 2, LangGraph, DeepAgents 0.7.32.

**Spec:** `docs/superpowers/specs/2026-11-03-solver-reliability-design.md`.

## Global constraints

- Preserve existing files and changes; no commits, publishing, deployment or dependency upgrades.
- Native engine only; no new agents, persistence abstractions or provider calls.
- Host checks remain authoritative. No candidate means no verified resolution.
- Every changed behavior has a regression observed failing before its fix.
- Optional agents import isolation, client identity, shared quotas and dry-run remain intact.

## Review focus

- Mixed candidate/action messages, including quota exhaustion and rejected first candidate.
- Constructed/copied models and mutable nested inputs crossing public boundaries.
- Native summary overflow retry respecting quota, cancellation and callback accounting.
- Partial usage, failed provider calls and reused model objects in paired evaluation.
- Reserved investigation cases discover changed paths/data through tools; expected results are independently graded.

### Task 1: Runtime boundaries and native routing

**Files:** `packages/workflow/src/bm_workflow/agents/{contracts,native,__init__}.py`,
`packages/workflow/tests/test_solver.py`, optional `test_solver_contracts.py`.

**Interfaces:** Preserve public signatures. Native middleware waits for tool results
before checking structured output. SolverContext snapshots/revalidates ProblemSpec;
builder revalidates SolverLimits; verifier receives isolated candidate data.

- [x] Add regressions: mixed actions execute exactly once; rejected mixed candidate can repair; exhausted tool budget cannot approve; invalid constructed problem/limits fail before effects; verifier mutation cannot corrupt returned candidate; no-candidate blocking is host-owned.
- [x] Observe failures using `uv run --locked --extra agents pytest tests/test_solver.py -q` from package directory.
- [x] Implement the smallest native hooks and boundary checks; prove explicit native handled-tool errors can recover while arbitrary effect errors remain terminal.
- [x] Reproduce and restore parent skill inheritance for the auto-supplied general-purpose specialist with bounded skill middleware; custom specialists keep explicit sources.
- [x] Reject host tools named `task` after reproducing native delegation shadowing; cover callable and BaseTool names without changing other native naming policies.
- [x] Re-run focused and full package tests; record output and decisions in the ledger.

### Task 2: Native summary recovery

**Files:** New `packages/workflow/src/bm_workflow/agents/summarization.py`,
new `packages/workflow/tests/test_solver_summarization.py`; Task 1 owner integrates `native.py`.

**Interfaces:** A bounded BaseChatModel adapter receives the host model and an active
SolverContext provider. Pass it as the native factory's summarizer; retain the
existing outer operation bound and native prompt/overflow behavior.

- [x] Test native context-overflow recovery and preserved media instructions, including quota one preventing a second call and quota two permitting recovery.
- [x] Observe failure of old custom summary path; implement wrapper that reserves each actual attempt, propagates config/callbacks and records available tokens.
- [x] Run native summary tests and full package tests; verify whole-operation backend deadlines still pass.

### Task 3: Representative and comparable evaluation

**Files:** `packages/workflow/examples/solver_benchmark.py`,
`packages/workflow/tests/test_solver_evaluation.py`; separate example data module if useful.

**Interfaces:** Preserve run_benchmark and aggregate summary API. Add partitioned
metrics, distinguish independently verified resolution from host verification,
mark partial token accounting and scripted-only perturbations explicitly.

- [x] Add failing tests for partial token usage, observed self-repair, shared model rejection, and comparable external verification.
- [x] Add a multi-source investigation case and a changed-data/path holdout. The model must use returned observations, never fixture answers; an independent grader checks resulting artifacts.
- [x] Implement metrics and cases; run `uv run --locked --extra agents pytest tests/test_solver_evaluation.py -q` and the benchmark CLI.
- [x] Record evidence without making claims about real-model reasoning quality.

### Task 4: Review, documentation and validation

**Files:** Package README, this plan, research/evaluation reports and ledger.

- [x] Obtain independent review of all changed boundaries and evaluation comparisons; fix material findings with regressions.
- [x] Document native error recovery, summary behavior, metric meanings, provider opt-in and remaining operational limits; link official sources.
- [x] Run package suites on Python 3.12/3.14, Ruff lint/format, strict mypy, quickstart, solver/team examples and benchmark. Check root regressions as appropriate.
- [x] Build/install a fresh wheel and verify optional import isolation (new production module), then diff/whitespace check. Preserve all changes uncommitted.
