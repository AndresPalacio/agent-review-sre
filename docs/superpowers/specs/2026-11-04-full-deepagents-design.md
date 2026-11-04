# Full Deep Agents Design

## Goal

Standardize every model-driven autonomous loop in `fin_graph` on
`deepagents.create_deep_agent` while retaining deterministic LangGraph workflows
for routing, approvals, fan-out, persistence, and financial side effects.

## Definition of "full Deep Agents"

- Every production agent factory under `src/fin_graph` resolves to
  `create_deep_agent`.
- No project code under `src/fin_graph` imports or calls
  `langchain.agents.create_agent`.
- Native Deep Agents delegation is used for synchronous compiled subagents and
  Agent Protocol async subagents.
- LangGraph remains the runtime and deterministic workflow layer. Replacing
  order routing, CAS persistence, effect ledgers, or fixed fan-out with model
  decisions is explicitly out of scope.

This definition follows Deep Agents' own architecture: `create_deep_agent`
assembles an opinionated harness and ultimately builds a LangChain agent on the
LangGraph runtime.

## Dependency policy

- The root project requires `deepagents>=0.7.32,<0.8` as a base dependency.
- Root optional extras no longer own the Deep Agents dependency.
- `packages/workflow` remains an independent package with its existing optional
  `agents` extra and standalone installation contract.
- The root lockfile is regenerated and the complete root suite is rerun after
  the upgrade.

## Agent construction policy

### Restricted default

`fin_graph.agents.common.factory.make_agent` remains the single seam for small
tool-calling agents but delegates to `create_deep_agent`. Its default boundary
replaces the additive filesystem and synchronous subagent middleware with empty
slots. This preserves the current allowlist: callers expose only the tools they
pass explicitly while still gaining Deep Agents state, summarization, patching,
prompt caching, and a consistent harness.

The boundary middleware moves from persona-specific code into a common module
and is reused by persona agents and the common factory.

### Native delegation

- `make_supervisor` converts compiled child graphs to `CompiledSubAgent` specs
  and delegates through the native `task` tool.
- The async solver declares `AsyncSubAgent` specs through the Deep Agents
  `subagents=` interface instead of manually installing
  `AsyncSubAgentMiddleware`.
- Synchronous delegation is opt-in at the common factory boundary. Async-only
  delegation keeps the default synchronous `general-purpose` tool hidden.
- Existing limits, context propagation, approvals, structured results,
  cancellation, and task IDs remain covered by regression tests.

## Catalog and architecture invariants

- `analysis.native_react` is classified as a Deep Agents subagent.
- The catalog no longer accepts `langchain_agent` as a kind.
- An AST architecture test rejects direct `create_agent` imports/calls anywhere
  under `src/fin_graph`.
- Existing Deep Agents factory discovery is extended to include the common
  factory.
- Deterministic persona-team metadata remains explicit: its children are Deep
  Agents, but the outer fan-out graph is deterministic infrastructure.

## Interactive architecture page

Add a responsive product page to the existing React/Vite interface. It contains:

1. A verdict explaining the full-Deep-Agents boundary.
2. An `Actual / Objetivo / Literal` architecture selector.
3. Clickable architecture nodes with implementation, file, and rationale.
4. A factory call-chain inspector.
5. A concurrency simulator for supervisors, children, and worker slots.
6. A gain/loss matrix and staged migration map.
7. Links to the official Deep Agents and LangGraph documentation.

The page uses the existing theme tokens, is keyboard accessible, and remains
usable at narrow widths. Claims about the repository are backed by local paths;
claims about Deep Agents are linked to official primary sources.

## Non-goals

- Replacing deterministic `StateGraph` workflows.
- Moving `packages/workflow` into the root runtime.
- Enabling host filesystem or shell access for restricted financial analysts.
- Removing LangChain or LangGraph dependencies, which Deep Agents itself uses.
- Publishing or pushing the branch without explicit user authorization.

## Acceptance criteria

- Root dependency and lock resolve Deep Agents `0.7.32`.
- `rg "from langchain.agents import create_agent|create_agent\\(" src/fin_graph`
  finds no project construction path.
- Restricted agents see only explicitly supplied tools.
- Common synchronous supervisors use the native `task` tool.
- Async solver behavior, two-child cap, updates, cancellation, and notifications
  remain green.
- Agent catalog contains only Deep Agents kinds for model-driven agents.
- The architecture page is reachable from the product bar and its three modes,
  node inspector, and concurrency warning are covered by UI tests.
- Python, frontend, typecheck, and build verification pass.

