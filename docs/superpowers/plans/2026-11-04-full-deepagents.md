# Full Deep Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Deep Agents the only harness used to construct autonomous model loops in `fin_graph`, preserve deterministic LangGraph control planes, and add an interactive architecture explainer.

**Architecture:** The common factory becomes a restricted wrapper over `create_deep_agent`, with native synchronous and asynchronous delegation enabled only by explicit callers. Deterministic workflows remain LangGraph graphs. The existing React application gains a tested architecture page that visualizes the current and target boundaries.

**Tech Stack:** Python 3.12, Deep Agents 0.7.32, LangChain, LangGraph, pytest, React 19, TypeScript, Vite, Vitest.

**Spec:** `docs/superpowers/specs/2026-11-04-full-deepagents-design.md`

## Global Constraints

- Every autonomous model loop under `src/fin_graph` must resolve to `deepagents.create_deep_agent`.
- No project code under `src/fin_graph` may import or call `langchain.agents.create_agent`.
- Keep order execution, approvals, CAS persistence, ledgers, and deterministic fan-out as LangGraph or ordinary code.
- Pin root Deep Agents to `>=0.7.32,<0.8`; do not change the standalone dependency contract of `packages/workflow`.
- Restricted financial agents must see only caller-supplied tools; filesystem, shell, and synchronous delegation remain hidden by default.
- Preserve async solver limits, task identity, updates, cancellation, context forwarding, and parent notifications.
- Do not push, merge, or publish the branch.

## Review Focus

- A restricted agent with one approved tool must not see `task`, filesystem, or shell tools; Task 2 adds a tool-surface regression test.
- A supervisor with compiled subagents must preserve runtime context, structured output, and interrupt/resume behavior; Task 4 retains and adapts those tests.
- An async-only supervisor must not gain the synchronous `general-purpose` subagent; Task 3 asserts its exposed tools and task specs.
- Existing checkpoints and typed responses must remain compatible after switching to `DeepAgentState`; Tasks 2 and 4 run checkpointer and structured-output tests.
- Narrow screens and worker under-provisioning must remain understandable; Task 5 tests mode controls, inspector content, and the worker warning.

---

### Task 1: Promote Deep Agents 0.7.32 to the root runtime

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `tests/compat/test_licensing.py`

**Interfaces:**
- Consumes: root package metadata and optional extras.
- Produces: a base installation containing `deepagents>=0.7.32,<0.8` for every later task.

- [ ] **Step 1: Write the failing dependency policy test**

Extend `test_optional_dependency_partitioning` to assert that `deepagents` is in
the base requirements and that its minimum version is at least `0.7.32`.

- [ ] **Step 2: Run the policy test and verify RED**

Run: `uv run pytest tests/compat/test_licensing.py::test_optional_dependency_partitioning -q`

Expected: FAIL because Deep Agents is currently owned by extras and pinned at `0.7.29`.

- [ ] **Step 3: Update dependency ownership and lock**

Add `deepagents>=0.7.32,<0.8` to base dependencies, remove redundant root-extra
copies, run `uv lock --upgrade-package deepagents`, and run `uv sync --all-extras`.
Leave `packages/workflow` unchanged.

- [ ] **Step 4: Verify GREEN and the resolved version**

Run: `uv run pytest tests/compat/test_licensing.py::test_optional_dependency_partitioning -q`

Expected: PASS. Then run `uv run python -c "import importlib.metadata as m; print(m.version('deepagents'))"` and expect `0.7.32`.

- [ ] **Step 5: Commit**

Commit: `build: promote deepagents to the root runtime`

### Task 2: Replace the common LangChain factory with a restricted Deep Agents seam

**Files:**
- Create: `src/fin_graph/agents/common/middleware/deepagents.py`
- Create: `tests/agents/common/middleware/test_deepagents.py`
- Modify: `src/fin_graph/agents/common/factory.py`
- Modify: `src/fin_graph/agents/analysis/personas.py`
- Modify: `src/fin_graph/agents/catalog.py`
- Modify: `src/fin_graph/agents/analysis/catalog.py`
- Modify: `tests/agents/common/test_factory.py`
- Modify: `tests/agents/test_catalog.py`
- Modify: `tests/architecture/test_agent_boundaries.py`

**Interfaces:**
- Consumes: Deep Agents 0.7.32 from Task 1.
- Produces: `restricted_deepagents_middleware() -> list[AgentMiddleware]` and an extended `make_agent(..., subagents=(), allow_synchronous_subagents=False)` that always calls `create_deep_agent`.

- [ ] **Step 1: Write failing architecture and tool-boundary tests**

Add tests that assert:

- no `langchain.agents.create_agent` import or call exists under `src/fin_graph`;
- the common factory appears in Deep Agents factory discovery;
- a `make_agent` instance passed only `noop` exposes only `noop` to its model;
- the shared restricted middleware replaces `FilesystemMiddleware` and
  `SubAgentMiddleware` by name;
- the catalog no longer accepts `langchain_agent` and classifies
  `analysis.native_react` as `deepagents_subagent`.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `uv run pytest tests/architecture/test_agent_boundaries.py tests/agents/common/test_factory.py tests/agents/common/middleware/test_deepagents.py tests/agents/test_catalog.py -q`

Expected: FAIL on the current `create_agent` factory, missing common middleware,
extra catalog kind, and missing tool-surface assertion.

- [ ] **Step 3: Implement the common boundary and factory**

Move the no-filesystem and no-synchronous-subagent replacement middleware into
the common module. Change `make_agent` to call `create_deep_agent`, prepend the
restricted boundary by default, accept Deep Agents subagent specs, and omit the
no-subagent replacement only when `allow_synchronous_subagents=True`.

Update personas to reuse the shared boundary. Update catalog kinds and metadata.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the same focused pytest command.

Expected: PASS with the model seeing no additive Deep Agents tools in restricted mode.

- [ ] **Step 5: Run all common middleware and analysis service tests**

Run: `uv run pytest tests/agents/common tests/workflows/analysis/test_services.py tests/workflows/analysis/test_extensions.py -q`

Expected: PASS, including structured output, masking, approvals, caching, and ReAct analysis.

- [ ] **Step 6: Commit**

Commit: `refactor: construct restricted agents with deepagents`

### Task 3: Use native Deep Agents async-subagent declarations in the solver

**Files:**
- Modify: `src/fin_graph/agents/solver/supervisor.py`
- Modify: `tests/agents/solver/test_supervisor.py`

**Interfaces:**
- Consumes: `make_agent(..., subagents=(), allow_synchronous_subagents=False)` from Task 2.
- Produces: an async solver supervisor assembled through `create_deep_agent` with two `AsyncSubAgent` specs.

- [ ] **Step 1: Write the failing assembly test**

Patch the common factory boundary in a unit test and assert that `_supervisor()`
passes exactly two `AsyncSubAgent` specs named `fundamentals` and `technicals`
through `subagents=`, with no manually supplied `AsyncSubAgentMiddleware`.

- [ ] **Step 2: Run the assembly and solver tests and verify RED**

Run: `uv run pytest tests/agents/solver/test_supervisor.py -q`

Expected: FAIL because the supervisor currently installs middleware manually.

- [ ] **Step 3: Migrate solver assembly**

Replace the explicit middleware construction with public `AsyncSubAgent` specs
passed through the common Deep Agents factory. Preserve `PilotGuard`, model-call
limits, graph IDs, descriptions, and the async-only restricted boundary.

- [ ] **Step 4: Run solver tests and verify GREEN**

Run: `uv run pytest tests/agents/solver/test_supervisor.py -q`

Expected: PASS, including two-child concurrency, context forwarding, updates,
cancellation, revisions, consolidation, and native notifications.

- [ ] **Step 5: Commit**

Commit: `refactor: declare solver children through deepagents`

### Task 4: Replace manual supervisor tools with native compiled subagents

**Files:**
- Modify: `src/fin_graph/agents/common/subagents.py`
- Modify: `tests/agents/common/test_subagents.py`

**Interfaces:**
- Consumes: synchronous-delegation opt-in from Task 2 and existing `SubagentSpec`.
- Produces: `make_supervisor` backed by Deep Agents `CompiledSubAgent` and the native `task` tool.

- [ ] **Step 1: Rewrite one delegation test for the desired native contract**

Change the smallest supervisor test so its scripted supervisor calls
`task(description=..., subagent_type="math_expert")`, and assert the final child
answer reaches the supervisor.

- [ ] **Step 2: Run that test and verify RED**

Run: `uv run pytest tests/agents/common/test_subagents.py::test_synchronous_invoke_also_delegates -q`

Expected: FAIL because the current supervisor exposes one tool per child rather than `task`.

- [ ] **Step 3: Implement native compiled delegation**

Convert every `SubagentSpec` to `CompiledSubAgent(name, description, runnable,
mode="isolated")` and call the common factory with
`allow_synchronous_subagents=True`. Remove the manual `StructuredTool` adapter
while retaining the result-rendering helper only if an external test or caller
still requires it.

- [ ] **Step 4: Adapt remaining supervisor scripts to the native task schema**

Update test model responses without weakening assertions for final-only output,
structured JSON, runtime context, checkpointer memory, or interrupt/resume.

- [ ] **Step 5: Run all supervisor tests and verify GREEN**

Run: `uv run pytest tests/agents/common/test_subagents.py -q`

Expected: PASS for synchronous/async invocation, typed results, context, and approvals.

- [ ] **Step 6: Commit**

Commit: `refactor: use native deepagents compiled subagents`

### Task 5: Add the interactive Deep Agents architecture page

**Files:**
- Create: `web/src/components/architecture/DeepAgentsArchitecture.tsx`
- Create: `web/src/components/architecture/DeepAgentsArchitecture.css`
- Create: `web/src/components/architecture/DeepAgentsArchitecture.test.tsx`
- Modify: `web/src/components/layout/TopBar.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/App.test.tsx`

**Interfaces:**
- Consumes: approved design spec and the final target topology from Tasks 1-4.
- Produces: a lazy-loaded `DeepAgentsArchitecture` product page and an `architecture` `ProductPage` value.

- [ ] **Step 1: Write failing interaction tests**

Test that the page:

- defaults to `Objetivo`;
- switches among `Actual`, `Objetivo`, and `Literal` and changes the verdict;
- opens a node inspector with an exact repository path;
- recalculates required slots as `1 + children` and shows a warning when workers are insufficient;
- exposes official Deep Agents and LangGraph links;
- is reachable from the product navigation.

- [ ] **Step 2: Run frontend tests and verify RED**

Run: `npm test -- --run src/components/architecture/DeepAgentsArchitecture.test.tsx src/App.test.tsx`

Expected: FAIL because the page and navigation entry do not exist.

- [ ] **Step 3: Implement the architecture page**

Build the responsive page with semantic buttons, range inputs, node inspector,
mode-specific topology data, gain/loss matrix, migration phases, and source
links. Use existing theme variables and a dedicated CSS file; do not add a
charting dependency.

- [ ] **Step 4: Run frontend tests and verify GREEN**

Run the same focused Vitest command.

Expected: PASS.

- [ ] **Step 5: Verify typecheck and production HTML build**

Run: `npm run typecheck && npm run build`

Expected: PASS and `src/fin_graph/server/static/index.html` generated by Vite.

- [ ] **Step 6: Commit**

Commit: `feat: explain the full deepagents architecture`

### Task 6: Align documentation and run whole-branch verification

**Files:**
- Modify: `README.md`
- Modify: `src/fin_graph/agents/README.md`
- Modify: `docs/arquitectura-agentes.md`
- Modify: any focused regression test required by an observed failure

**Interfaces:**
- Consumes: completed runtime and UI behavior from Tasks 1-5.
- Produces: user-facing architecture documentation and a fully verified branch.

- [ ] **Step 1: Write the failing documentation invariant**

Extend an architecture/catalog test to assert that documentation names the
common factory as Deep Agents, describes deterministic LangGraph as the control
plane, and does not describe `analysis.native_react` as a LangChain agent.

- [ ] **Step 2: Run the invariant and verify RED**

Run: `uv run pytest tests/architecture/test_agent_boundaries.py tests/agents/test_catalog.py -q`

Expected: FAIL on stale architecture wording.

- [ ] **Step 3: Update documentation**

Document the full-Deep-Agents definition, restricted factory, native sync/async
delegation, retained deterministic graphs, dependency version, and how to open
the architecture page.

- [ ] **Step 4: Run focused verification and verify GREEN**

Run the same focused pytest command.

Expected: PASS.

- [ ] **Step 5: Run complete verification**

Run:

- `uv run ruff check .`
- `uv run mypy src/fin_graph`
- `uv run pytest -q`
- `npm test -- --run` from `web`
- `npm run typecheck` from `web`
- `npm run build` from `web`
- `rg -n "from langchain\\.agents import create_agent|\\bcreate_agent\\(" src/fin_graph`

Expected: all commands pass; the final `rg` returns no matches.

- [ ] **Step 6: Commit**

Commit: `docs: define the full deepagents boundary`

