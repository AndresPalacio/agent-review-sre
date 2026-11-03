# Multi-Agent Code Organization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganizar fin-graph para que agentes, workflows, runtime y optimización sean encontrables y comprensibles desde el árbol del código, sin cambiar resultados ni contratos públicos.

**Architecture:** Las fábricas agentivas vivirán bajo `fin_graph.agents`, las máquinas de estado bajo `fin_graph.workflows`, el ensamblaje productivo bajo `fin_graph.runtime` y el ciclo opcional de better-harness bajo `fin_graph.optimization.harness`. Un catálogo inmutable agregará descriptores declarados junto a las fábricas, mientras producción conservará imports explícitos y tipados.

**Tech Stack:** Python 3.12, DeepAgents, LangChain, LangGraph, Typer/Rich, Pydantic, pytest, import-linter, uv/Hatch.

**Spec:** `docs/superpowers/specs/2026-11-03-multi-agent-code-organization-design.md`

## Global Constraints

- Python permanece en `>=3.12,<3.13`; no se actualizan dependencias durante esta refactorización.
- No cambiar prompts, herramientas, scoring, decisiones financieras, contratos HTTP/SSE, persistencia ni compatibilidad AI Hedge Fund.
- Preservar `src.*`, `v2.*`, `app.backend.*`, comandos CLI, rutas HTTP y schemas públicos.
- Toda llamada a `create_deep_agent`, incluida la del proponente opcional, debe quedar bajo `src/fin_graph/agents/`.
- Producción no puede importar `fin_graph.optimization` ni `better_harness`; `src/fin_graph/cli/harness.py` solo puede hacerlo de forma perezosa al ejecutar un comando opcional.
- `optimization/fin.toml` es configuración opcional del checkout y no se incluye como configuración operativa del wheel.
- No dejar lógica duplicada ni shims internos; el único shim nuevo permitido es `fin_graph.work.graphs`, porque `examples/langgraph.async.json` documentó esa superficie.
- Cada movimiento debe conservar firmas y comportamiento, y terminar con pruebas verdes antes del commit.

## Review Focus

- Instalación desde wheel fuera del checkout y sin extra `harness`: importar CLI/app/evals debe funcionar y el TOML opcional no debe aparecer en el wheel (Task 8).
- ID duplicado, hijo inexistente o `factory_path` obsoleto en el catálogo: la validación debe fallar antes de mostrar datos incompletos (Task 4).
- `fin-graph harness validate` sin ruta fuera del checkout: debe salir con código 2 y explicar cómo pasar `--config`, sin traceback ni secreto (Task 8).
- Alias, imports calificados y comentarios que mencionan `create_deep_agent`: la prueba arquitectónica debe analizar AST y detectar llamadas reales sin falsos positivos de texto (Task 8).
- Worker/proponente lanzado en Windows después del cambio de módulo: los subprocess deben usar `fin_graph.optimization.harness.worker` y `fin_graph.optimization.harness.proposer` (Task 8).

---

## File Map

| Responsabilidad | Origen | Destino |
|---|---|---|
| Fábrica, subagentes, middleware y modelo scripted | `src/fin_graph/agents/{factory.py,subagents.py,middleware/,testing/}` | `src/fin_graph/agents/common/` |
| Catálogo de analistas reactivos | `src/fin_graph/graphs/analysis/analysts.py` | `src/fin_graph/agents/analysis/catalog.py` |
| Personas y supervisor compatible | `src/fin_graph/graphs/analysis/{personas.py,persona_prompts.py,compat.py}` | `src/fin_graph/agents/analysis/{personas.py,persona_prompts.py,supervisor.py}` |
| Chat DeepAgents | `src/fin_graph/graphs/chat/graph.py` | `src/fin_graph/agents/chat/supervisor.py` |
| Solver verificado | `src/fin_graph/work/graphs.py` | `src/fin_graph/agents/solver/supervisor.py` |
| DeepAgent proponente | bloque de `run_outer_agent` en `src/fin_graph/harness/proposer.py` | `src/fin_graph/agents/optimizer/proposer.py` |
| Workflow de análisis | resto de `src/fin_graph/graphs/analysis/` | `src/fin_graph/workflows/analysis/` |
| Servicio paper compartido | `src/fin_graph/graphs/orders/desk.py` | `src/fin_graph/trading/order_desk.py` |
| Workflows de órdenes y market watch | `src/fin_graph/graphs/orders/graph.py`, `src/fin_graph/graphs/market_watch/graph.py` | `src/fin_graph/workflows/{orders,market_watch}/` |
| Runtime productivo | `src/fin_graph/harness/{runtime,observation,profile,trace}.py` | `src/fin_graph/runtime/{analysis,observation,profile,trace}.py` |
| Optimizador opcional | resto de `src/fin_graph/harness/` | `src/fin_graph/optimization/harness/` |
| Configuración opcional | `harness/fin.toml` | `optimization/fin.toml` |

Los tests siguen el mismo mapa: infraestructura en `tests/agents/common`, agentes especializados en `tests/agents/{analysis,chat,solver,optimizer}`, workflows en `tests/workflows`, runtime en `tests/runtime` y better-harness en `tests/optimization/harness`.

---

### Task 1: Consolidar infraestructura común de agentes

**Files:**
- Create: `src/fin_graph/agents/common/__init__.py`
- Move: `src/fin_graph/agents/factory.py` → `src/fin_graph/agents/common/factory.py`
- Move: `src/fin_graph/agents/subagents.py` → `src/fin_graph/agents/common/subagents.py`
- Move: `src/fin_graph/agents/middleware/` → `src/fin_graph/agents/common/middleware/`
- Move: `src/fin_graph/agents/testing/` → `src/fin_graph/agents/common/testing/`
- Move: `tests/agents/test_{factory,subagents,scripted_model}.py` and `tests/agents/middleware/` → `tests/agents/common/`
- Modify: every source/test returned by `rg -l "fin_graph\.agents\.(factory|subagents|middleware|testing)" src tests`
- Test: `tests/architecture/test_package_layout.py`

**Interfaces:**
- Consumes: existing LangChain/DeepAgents types only.
- Produces: unchanged `make_agent(...)`, `SubagentSpec`, `subagent_tool(...)`, `make_supervisor(...)`, all middleware classes, `ScriptedChatModel` and `tool_call_message(...)` under `fin_graph.agents.common.*`.

- [ ] **Step 1: Write the failing target-layout test**

```python
@pytest.mark.parametrize("module", [
    "fin_graph.agents.common.factory",
    "fin_graph.agents.common.subagents",
    "fin_graph.agents.common.middleware.guardrails",
    "fin_graph.agents.common.testing.scripted_model",
])
def test_target_module_imports(module: str) -> None:
    assert importlib.import_module(module).__name__ == module
```

- [ ] **Step 2: Run the new test and observe the missing package**

Run: `uv run pytest tests/architecture/test_package_layout.py -q`

Expected: FAIL with `ModuleNotFoundError: fin_graph.agents.common`.

- [ ] **Step 3: Relocate the common modules without compatibility shims**

Preserve every public signature and body; only package docstrings/import paths change.

- [ ] **Step 4: Update all repository imports and move the matching tests**

Run: `rg -n "fin_graph\.agents\.(factory|subagents|middleware|testing)" src tests`

Expected: no matches.

- [ ] **Step 5: Verify the common agent layer**

Run: `uv run pytest tests/agents/common tests/spikes/test_middleware_async_contract.py tests/spikes/test_subgraph_node_interrupt.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/fin_graph/agents tests/agents tests/architecture src tests
git commit -m "refactor(agents): group shared agent infrastructure"
```

### Task 2: Mover personas y supervisor de análisis a `agents/analysis`

**Files:**
- Create: `src/fin_graph/agents/analysis/__init__.py`
- Move: `src/fin_graph/graphs/analysis/analysts.py` → `src/fin_graph/agents/analysis/catalog.py`
- Move: `src/fin_graph/graphs/analysis/personas.py` → `src/fin_graph/agents/analysis/personas.py`
- Move: `src/fin_graph/graphs/analysis/persona_prompts.py` → `src/fin_graph/agents/analysis/persona_prompts.py`
- Move: `src/fin_graph/graphs/analysis/compat.py` → `src/fin_graph/agents/analysis/supervisor.py`
- Move: `tests/graphs/analysis/test_persona_team.py` → `tests/agents/analysis/test_supervisor.py`
- Modify: consumers returned by `rg -l "fin_graph\.graphs\.analysis\.(analysts|personas|persona_prompts|compat)" src tests`
- Modify: `tests/architecture/test_package_layout.py`

**Interfaces:**
- Consumes: `analyst_catalog()` as the only source of the 19 identities; common middleware and `RunContext`.
- Produces: `make_persona_agent(model, spec, *, masking=None) -> CompiledStateGraph[Any, Any, Any, Any]` and the unchanged `make_compat_analysis_agent(...)` signature.

- [ ] **Step 1: Extend the layout test with the four analysis-agent modules**

Assert that `fin_graph.agents.analysis.catalog`, `.personas`, `.persona_prompts` and `.supervisor` import.

- [ ] **Step 2: Run the layout and persona tests before moving code**

Run: `uv run pytest tests/architecture/test_package_layout.py tests/graphs/analysis/test_persona_team.py -q`

Expected: layout test FAILS because `fin_graph.agents.analysis` does not exist; characterization tests pass.

- [ ] **Step 3: Move the four modules and extract the persona factory**

`make_compat_analysis_agent(...)` must call `make_persona_agent(...)` inside its existing 19-item loop; move the existing `create_deep_agent(...)` arguments unchanged into that helper.

- [ ] **Step 4: Update consumers and move the persona characterization suite**

Run: `rg -n "fin_graph\.graphs\.analysis\.(analysts|personas|persona_prompts|compat)" src tests`

Expected: no matches.

- [ ] **Step 5: Verify identities, boundaries and compatible output**

Run: `uv run pytest tests/domain/test_analyst_catalog.py tests/agents/analysis tests/compat/v1/test_portfolio_parity.py -q`

Expected: PASS, including exactly 19 ordered identities.

- [ ] **Step 6: Commit**

```bash
git add src/fin_graph/agents/analysis src/fin_graph/graphs/analysis tests/agents/analysis tests/graphs/analysis tests/architecture src tests
git commit -m "refactor(agents): make analysis personas discoverable"
```

### Task 3: Mover chat, solver y proponente DeepAgents

**Files:**
- Create: `src/fin_graph/agents/{chat,solver,optimizer}/__init__.py`
- Move: `src/fin_graph/graphs/chat/graph.py` → `src/fin_graph/agents/chat/supervisor.py`
- Move logic: `src/fin_graph/work/graphs.py` → `src/fin_graph/agents/solver/supervisor.py`
- Move: `src/fin_graph/work/testing.py` → `src/fin_graph/agents/solver/testing.py`
- Extract: `read_control(...)` from `src/fin_graph/work/store.py` → `src/fin_graph/agents/solver/control.py`
- Modify: `src/fin_graph/work/store.py`
- Replace: `src/fin_graph/work/graphs.py` with one deprecated re-export import for `supervisor`, `fundamentals`, `technicals`
- Create: `src/fin_graph/agents/optimizer/proposer.py`
- Modify: `src/fin_graph/harness/proposer.py`, `examples/langgraph.async.json`
- Move: `tests/graphs/chat/` → `tests/agents/chat/`
- Move: `tests/work/test_graphs.py` → `tests/agents/solver/test_supervisor.py`
- Create: `tests/agents/optimizer/test_proposer.py`
- Modify: `tests/architecture/test_package_layout.py`

**Interfaces:**
- Consumes: common factory/middleware, solver-local control/testing helpers and optional proposer inputs; no import from `fin_graph.work` or `fin_graph.workflows`.
- Produces: unchanged `make_chat_graph(...)`; unchanged async `supervisor(config)`, `fundamentals(config)`, `technicals(config)`; `make_solver_child(role: Literal["fundamentals", "technicals"]) -> CompiledStateGraph[...]`; `make_optimizer_agent(*, model, system_prompt: str, workspace_root: Path) -> Any`.

- [ ] **Step 1: Add failing import/factory tests**

```python
def test_optimizer_factory_forwards_only_bounded_workspace(monkeypatch, tmp_path) -> None:
    import fin_graph.agents.optimizer.proposer as module
    captured: dict[str, object] = {}
    model = object()
    sentinel = object()
    monkeypatch.setattr(
        module, "FilesystemBackend", lambda **kwargs: ("backend", kwargs)
    )
    monkeypatch.setattr(
        module, "create_deep_agent", lambda **kwargs: captured.update(kwargs) or sentinel
    )
    graph = module.make_optimizer_agent(
        model=model, system_prompt="bounded", workspace_root=tmp_path
    )
    assert graph is sentinel
    assert captured == {
        "model": model,
        "system_prompt": "bounded",
        "backend": ("backend", {"root_dir": str(tmp_path), "virtual_mode": True}),
    }

def test_legacy_work_graph_import_is_the_new_solver() -> None:
    from fin_graph.agents.solver.supervisor import supervisor as current
    from fin_graph.work.graphs import supervisor as legacy
    assert legacy is current
```

- [ ] **Step 2: Run the new imports and existing characterization tests**

Run: `uv run pytest tests/architecture/test_package_layout.py tests/graphs/chat tests/work/test_graphs.py tests/agents/optimizer/test_proposer.py -q`

Expected: new paths FAIL; existing chat/solver tests pass.

- [ ] **Step 3: Relocate chat and solver, retaining only the documented work shim**

Make `_child(...)` public as `make_solver_child(...)`; update `examples/langgraph.async.json` to the new three targets.

- [ ] **Step 4: Extract the optional proposer factory**

Move only model/prompt/backend construction and the `create_deep_agent(...)` call to `agents/optimizer/proposer.py`; keep workspace preparation, better-harness types and proposal lifecycle in the current harness module for now.

- [ ] **Step 5: Update imports and relocate tests**

Run: `rg -n "fin_graph\.graphs\.chat|fin_graph\.work\.(testing|store)|from fin_graph\.work import graphs" src/fin_graph/agents tests examples`

Expected: no production consumer uses the old paths; the compatibility test intentionally imports `fin_graph.work.graphs`.

- [ ] **Step 6: Verify all three specialized areas**

Run: `uv run pytest tests/agents/chat tests/agents/solver tests/agents/optimizer tests/work/test_service.py tests/server/test_chat_api.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/fin_graph/agents src/fin_graph/work src/fin_graph/graphs/chat src/fin_graph/harness/proposer.py examples tests
git commit -m "refactor(agents): centralize chat solver and optimizer agents"
```

### Task 4: Crear el catálogo autoritativo de agentes

**Files:**
- Create: `src/fin_graph/agents/catalog.py`
- Modify: `src/fin_graph/agents/analysis/catalog.py`
- Modify: `src/fin_graph/agents/chat/supervisor.py`
- Modify: `src/fin_graph/agents/solver/supervisor.py`
- Modify: `src/fin_graph/agents/optimizer/proposer.py`
- Create: `tests/agents/test_catalog.py`

**Interfaces:**
- Produces: frozen `AgentDescriptor(id: str, display_name: str, kind: AgentKind, purpose: str, factory_path: str, children: tuple[str, ...] = (), tool_policy: ToolPolicy = "read_only", middleware: tuple[str, ...] = (), limits: tuple[str, ...] = (), activation: str = "", surfaces: tuple[str, ...] = ())`.
- Produces: `validate_agent_catalog(items: Sequence[AgentDescriptor]) -> tuple[AgentDescriptor, ...]`, `agent_catalog() -> tuple[AgentDescriptor, ...]`, `agent_by_id(agent_id: str) -> AgentDescriptor`, `resolve_factory(descriptor: AgentDescriptor) -> Callable[..., Any]`, `AgentDescriptor.to_dict() -> dict[str, object]`.
- `kind` is exactly `deepagents_supervisor | deepagents_subagent | langchain_agent`; `tool_policy` is exactly `read_only | paper_trade | workspace`.

- [ ] **Step 1: Write catalog contract tests**

```python
def test_catalog_has_unique_resolvable_agents_and_nineteen_personas() -> None:
    catalog = agent_catalog()
    assert len({item.id for item in catalog}) == len(catalog)
    assert len([item for item in catalog if item.id.startswith("analysis.persona.")]) == 19
    assert all(callable(resolve_factory(item)) for item in catalog)
    ids = {item.id for item in catalog}
    assert all(set(item.children) <= ids for item in catalog)

def test_catalog_includes_optional_optimizer_but_no_workflow_nodes() -> None:
    ids = {item.id for item in agent_catalog()}
    assert "optimizer.proposer" in ids
    assert not ids & {"analysis.verify", "analysis.risk", "orders.route", "market_watch.poll"}

def test_invalid_catalog_fails_closed() -> None:
    baseline = agent_catalog()
    with pytest.raises(ValueError, match="duplicate"):
        validate_agent_catalog((*baseline, baseline[0]))
    with pytest.raises(ValueError, match="child"):
        validate_agent_catalog((replace(baseline[0], children=("missing",)), *baseline[1:]))
    with pytest.raises(ImportError):
        validate_agent_catalog(
            (replace(baseline[0], factory_path="missing.module:factory"), *baseline[1:])
        )
```

- [ ] **Step 2: Run tests to verify the missing catalog**

Run: `uv run pytest tests/agents/test_catalog.py -q`

Expected: FAIL with `ModuleNotFoundError: fin_graph.agents.catalog`.

- [ ] **Step 3: Implement the frozen descriptor and lazy aggregation**

Each specialized package declares its tuple beside its real factory. `agent_catalog()` imports those tuples lazily, validates uniqueness/children/factory paths once, and caches the immutable result; runtime code does not call `resolve_factory`.

- [ ] **Step 4: Derive the 19 persona descriptors from `analyst_catalog()`**

Use IDs `analysis.persona.<analyst_id>` and one shared real factory path `fin_graph.agents.analysis.supervisor:make_persona_agent`; do not copy names, order, prompts or domains.

- [ ] **Step 5: Add descriptors for compatible analysis, native react analyst, chat, solver supervisor/children and optimizer proposer**

Children are the 19 persona IDs for compatible analysis and `solver.fundamentals`/`solver.technicals` for the solver supervisor.

- [ ] **Step 6: Verify catalog determinism and domain parity**

Run: `uv run pytest tests/agents/test_catalog.py tests/domain/test_analyst_catalog.py tests/skills/test_catalog_parity.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/fin_graph/agents tests/agents/test_catalog.py
git commit -m "feat(agents): add executable agent catalog"
```

### Task 5: Mover el workflow de análisis

**Files:**
- Create: `src/fin_graph/workflows/__init__.py`, `src/fin_graph/workflows/analysis/__init__.py`
- Move: `src/fin_graph/graphs/analysis/{graph,state,services,evidence,snapshots,prompts,extensions}.py` → `src/fin_graph/workflows/analysis/`
- Move: `src/fin_graph/graphs/analysis/strategy/` → `src/fin_graph/workflows/analysis/strategy/`
- Move: `tests/graphs/analysis/{conftest.py,test_graph.py,test_services.py}` → `tests/workflows/analysis/`
- Move: `tests/harness/test_extensions.py` → `tests/workflows/analysis/test_extensions.py`
- Modify: all source/test imports of the moved analysis modules
- Modify: current optimizer surface paths in `harness/fin.toml` and `src/fin_graph/harness/surfaces.py`
- Modify: `tests/architecture/test_package_layout.py`

**Interfaces:**
- Consumes: `agents.analysis.catalog`, common agent factory/middleware and domain/integration services.
- Produces: unchanged `_compile_analysis_graph(...)`, `AnalysisState`, `AnalysisServices`, `LangChainAnalysisServices`, evidence helpers, prompts and strategy hooks under `fin_graph.workflows.analysis`.

- [ ] **Step 1: Add target workflow imports to the layout test**

Assert `fin_graph.workflows.analysis.graph`, `.services`, `.state` and `.strategy.registration` import.

- [ ] **Step 2: Run layout plus current workflow characterization tests**

Run: `uv run pytest tests/architecture/test_package_layout.py tests/graphs/analysis/test_graph.py tests/graphs/analysis/test_services.py tests/harness/test_extensions.py -q`

Expected: target imports FAIL; characterization tests pass.

- [ ] **Step 3: Relocate analysis workflow modules without behavior edits**

Keep node names, state keys, retry/risk/order limits and event payloads byte-compatible.

- [ ] **Step 4: Update every consumer and optimizer surface import path**

Run: `rg -n "fin_graph\.graphs\.analysis" src tests harness`

Expected: no matches.

- [ ] **Step 5: Verify native analysis, compatible scoring and eval trajectory**

Run: `uv run pytest tests/workflows/analysis tests/agents/analysis tests/evals/market tests/compat/v1/test_portfolio_parity.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/fin_graph/workflows src/fin_graph/graphs/analysis tests/workflows tests/graphs/analysis tests/harness harness/fin.toml src tests
git commit -m "refactor(workflows): separate analysis state machine from agents"
```

### Task 6: Mover workflows de órdenes y market watch

**Files:**
- Create: `src/fin_graph/workflows/{orders,market_watch}/__init__.py`
- Move: `src/fin_graph/graphs/orders/graph.py` → `src/fin_graph/workflows/orders/graph.py`
- Create: `src/fin_graph/trading/__init__.py`
- Move: `src/fin_graph/graphs/orders/desk.py` → `src/fin_graph/trading/order_desk.py`
- Move: `src/fin_graph/graphs/market_watch/graph.py` → `src/fin_graph/workflows/market_watch/graph.py`
- Move: `tests/graphs/orders/test_order_graph.py` → `tests/workflows/orders/test_order_graph.py`
- Modify: CLI, scheduler, server, web fixtures and tests importing old graph paths
- Delete: empty `src/fin_graph/graphs/`, `tests/graphs/`
- Modify: `tests/architecture/test_package_layout.py`

**Interfaces:**
- Produces: unchanged `OrderDesk` under `fin_graph.trading.order_desk`; unchanged `make_order_graph(...)`, order node constants and market-watch graph factory under `fin_graph.workflows.*`.

- [ ] **Step 1: Add failing imports for orders and market watch**

Assert `fin_graph.trading.order_desk`, `fin_graph.workflows.orders.graph` and `fin_graph.workflows.market_watch.graph` import.

- [ ] **Step 2: Run current order/server characterization tests**

Run: `uv run pytest tests/graphs/orders tests/cli/test_orders.py tests/server/test_orders_api.py tests/scheduler/test_market_jobs.py -q`

Expected: existing tests pass and target imports fail.

- [ ] **Step 3: Relocate both workflows and update all consumers**

Run: `rg -n "fin_graph\.graphs" src tests examples langgraph.json`

Expected: no matches.

The chat, CLI, server and tests import `OrderDesk` from `fin_graph.trading.order_desk`; the orders
workflow imports the same class and does not re-export a duplicate.

- [ ] **Step 4: Remove the empty legacy graph packages**

Do not add import shims: these were internal paths and all repository consumers now use `workflows`.

- [ ] **Step 5: Verify orders, scheduler, server and chat integration**

Run: `uv run pytest tests/workflows tests/cli/test_orders.py tests/server/test_orders_api.py tests/server/test_gateway_api.py tests/scheduler/test_market_jobs.py tests/agents/chat -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/fin_graph/workflows src/fin_graph/graphs src/fin_graph/trading src/fin_graph/cli src/fin_graph/server src/fin_graph/scheduler tests
git commit -m "refactor(workflows): expose orders and market watch explicitly"
```

### Task 7: Separar el runtime productivo del harness

**Files:**
- Create: `src/fin_graph/runtime/__init__.py`
- Move: `src/fin_graph/harness/runtime.py` → `src/fin_graph/runtime/analysis.py`
- Move: `src/fin_graph/harness/{observation,profile,trace}.py` → `src/fin_graph/runtime/`
- Move: `tests/harness/{test_runtime,test_trace,test_entrypoints}.py` → `tests/runtime/`
- Modify: CLI, compat API, eval runner, server factory/app and all test fixtures consuming runtime
- Modify: `langgraph.json`
- Modify: `tests/test_langgraph_json.py`, `tests/architecture/test_package_layout.py`

**Interfaces:**
- Produces: unchanged `make_analysis_graph(...)`, `make_analysis_runtime[_async]()`, `make_analysis_graph_async()`, `make_compat_analysis_runtime[_async]()`, `_analysis_services(...)`, `ExecutionTrace`, `Observation` and `resolve_analysis_profile(...)` under `fin_graph.runtime`.

- [ ] **Step 1: Add failing runtime import and Studio target assertions**

```python
def test_langgraph_studio_uses_product_runtime() -> None:
    assert _graph_targets()["analysis"] == "./src/fin_graph/runtime/analysis.py:make_analysis_graph_async"
```

- [ ] **Step 2: Run runtime and Studio tests before the move**

Run: `uv run pytest tests/architecture/test_package_layout.py tests/test_langgraph_json.py tests/harness/test_runtime.py tests/harness/test_trace.py -q`

Expected: new runtime path assertions FAIL; old runtime characterization passes.

- [ ] **Step 3: Relocate runtime modules and switch product entry points**

Update explicit imports in CLI, server, compatibility Python API and evals; do not create `fin_graph.harness.runtime` shims.

- [ ] **Step 4: Point Studio at `runtime/analysis.py` and relocate runtime tests**

Run: `rg -n "fin_graph\.harness\.(runtime|profile|observation|trace)" src tests langgraph.json`

Expected: no matches.

- [ ] **Step 5: Verify all product entry points without loading better-harness**

Run: `uv run pytest tests/runtime tests/test_langgraph_json.py tests/cli/test_analyze.py tests/server/test_api.py tests/evals/market/test_equity_evals.py -q`

Expected: PASS; runtime CLI test proves optional optimizer imports are not needed.

- [ ] **Step 6: Commit**

```bash
git add src/fin_graph/runtime src/fin_graph/harness src/fin_graph/cli src/fin_graph/server src/fin_graph/compat src/fin_graph/evals tests langgraph.json
git commit -m "refactor(runtime): separate production assembly from optimization"
```

### Task 8: Aislar better-harness y proteger las dependencias

**Files:**
- Create: `src/fin_graph/optimization/__init__.py`, `src/fin_graph/optimization/harness/__init__.py`
- Move: remaining `src/fin_graph/harness/{artifacts,configuration,dataset.json,dataset,execution,experiment,proposer,quality,runner,surfaces,worker}.py` → `src/fin_graph/optimization/harness/`
- Move: remaining `tests/harness/` → `tests/optimization/harness/`
- Move: `harness/fin.toml` → `optimization/fin.toml`
- Modify: `src/fin_graph/cli/harness.py`, worker/proposer subprocess module names, TOML surface paths
- Modify: `pyproject.toml`
- Modify: `tests/packaging/test_clean_install.py`
- Create: `tests/architecture/test_agent_boundaries.py`, `tests/architecture/test_dependency_boundaries.py`
- Delete: empty `src/fin_graph/harness/`, `tests/harness/`, root `harness/`

**Interfaces:**
- Produces: optional modules under `fin_graph.optimization.harness.*` only.
- Produces in CLI: `_config_path(argument: Path | None, option: Path | None, *, cwd: Path | None = None) -> Path`; positional config remains compatible and `--config` is added.
- Produces in architecture tests: module constants `SRC: Path`, `PRODUCTION_ROOTS: tuple[Path, ...]`; `_create_deep_agent_call_paths(root: Path) -> set[str]`, `_create_deep_agent_factory_paths(root: Path) -> set[str]`, `_package_imports(root: Path) -> set[str]` (normalized to `fin_graph.<package>`) and `_forbidden_imports(roots: Sequence[Path], *, allowed: set[str]) -> list[str]`, all based on AST.

- [ ] **Step 1: Write failing AST/dependency tests**

```python
def test_create_deep_agent_calls_live_only_under_agents() -> None:
    assert discovered_create_deep_agent_call_paths() == {
        "agents/analysis/supervisor.py",
        "agents/chat/supervisor.py",
        "agents/solver/supervisor.py",
        "agents/optimizer/proposer.py",
    }

def test_production_has_no_optimization_or_better_harness_imports() -> None:
    assert _forbidden_imports(PRODUCTION_ROOTS, allowed={"cli/harness.py"}) == []

def test_agents_do_not_depend_on_workflows_runtime_or_work() -> None:
    assert _package_imports(SRC / "agents").isdisjoint(
        {"fin_graph.workflows", "fin_graph.runtime", "fin_graph.work"}
    )

def test_comments_do_not_count_as_deepagent_calls(tmp_path) -> None:
    (tmp_path / "comment.py").write_text("# create_deep_agent()\n", encoding="utf-8")
    (tmp_path / "aliased.py").write_text(
        "from deepagents import create_deep_agent as build\nbuild()\n", encoding="utf-8"
    )
    assert _create_deep_agent_call_paths(tmp_path) == {"aliased.py"}

def test_every_deepagent_factory_is_catalogued() -> None:
    catalogued = {item.factory_path for item in agent_catalog()}
    assert _create_deep_agent_factory_paths(SRC) <= catalogued
```

- [ ] **Step 2: Write failing optional-config CLI/packaging tests**

Assert explicit positional and `--config` paths work; both at once fail; omitted config in an empty cwd exits 2 mentioning `--config`; the wheel contains neither `optimization/fin.toml` nor result/database/secret files.
In the extracted-wheel subprocess, guard `builtins.__import__` so importing the CLI, server app and
`fin_graph.evals.market.runner` raises if any path attempts to import `better_harness`.

- [ ] **Step 3: Run architecture and harness tests before relocation**

Run: `uv run pytest tests/architecture tests/harness tests/packaging/test_clean_install.py -q`

Expected: FAIL on old harness imports, proposer call location and default config behavior.

- [ ] **Step 4: Relocate optional modules/config and update internal subprocess targets**

Use `fin_graph.optimization.harness.worker` and `.proposer`; keep all imports of `better_harness` under this package.

- [ ] **Step 5: Implement explicit/default config resolution in the CLI adapter**

Use `optimization/fin.toml` only when it exists under `cwd`; otherwise fail with an actionable message. Keep imports of optional modules inside command functions after path resolution.

- [ ] **Step 6: Update import-linter contracts**

Set the product layers to `runtime → workflows → agents → trading → integrations/safety/domain → llm/persistence/config` and add a forbidden dependency from product packages to `fin_graph.optimization`.

- [ ] **Step 7: Verify optional and base installations**

Run: `uv run pytest tests/architecture tests/optimization/harness tests/packaging/test_clean_install.py -q`

Run: `uv run lint-imports`

Expected: both PASS.

- [ ] **Step 8: Confirm old packages and references are gone**

Run: `rg -n "fin_graph\.harness|harness/fin\.toml|create_deep_agent" src tests optimization pyproject.toml`

Expected: no `fin_graph.harness`; config references use `optimization/fin.toml`; every real `create_deep_agent` call is under `src/fin_graph/agents`.

- [ ] **Step 9: Commit**

```bash
git add src/fin_graph/optimization src/fin_graph/agents src/fin_graph/cli src/fin_graph/harness optimization harness tests pyproject.toml
git commit -m "refactor(optimization): isolate optional better harness"
```

### Task 9: Añadir navegación CLI y documentación junto al código

**Files:**
- Create: `src/fin_graph/cli/agents.py`
- Modify: `src/fin_graph/cli/main.py`
- Create: `src/fin_graph/agents/README.md`
- Create: `src/fin_graph/agents/{analysis,chat,solver,optimizer}/README.md`
- Create: `src/fin_graph/workflows/README.md`, `src/fin_graph/runtime/README.md`
- Create: `src/fin_graph/optimization/README.md`, `optimization/README.md`
- Replace: `docs/arquitectura-agentes.md`
- Modify: `docs/better-harness.md`, `README.md`, `docs/compatibility/runtime-capabilities.md`
- Create: `tests/cli/test_agents.py`
- Modify: `tests/compat/test_documented_commands.py`, `tests/test_no_sre_product.py`

**Interfaces:**
- Produces: Typer group `agents` with `list --json` and `describe <id> --json`; both are read-only and use only `fin_graph.agents.catalog`.

- [ ] **Step 1: Write failing CLI tests**

```python
def test_agents_list_is_catalog_backed_and_has_nineteen_personas() -> None:
    result = runner.invoke(app, ["agents", "list", "--json"])
    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert len([item for item in payload if item["id"].startswith("analysis.persona.")]) == 19

def test_agents_describe_returns_relationships_without_building_a_model() -> None:
    result = runner.invoke(app, ["agents", "describe", "analysis.compatibility", "--json"])
    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert len(payload["children"]) == 19

def test_agents_describe_unknown_id_is_actionable() -> None:
    result = runner.invoke(app, ["agents", "describe", "missing"])
    assert result.exit_code == 2
    assert "missing" in result.output
```

- [ ] **Step 2: Run CLI/documentation tests before implementation**

Run: `uv run pytest tests/cli/test_agents.py tests/compat/test_documented_commands.py tests/test_no_sre_product.py -q`

Expected: FAIL because the `agents` command and new documentation do not exist.

- [ ] **Step 3: Implement the read-only Typer group**

Default list output is a compact Rich table; `--json` emits `AgentDescriptor.to_dict()`. `describe` never resolves/builds a model and converts unknown IDs into `typer.Exit(2)`.

- [ ] **Step 4: Write the colocated architecture guides**

Every specialized README has exactly these navigation sections: entrada, estado, agentes, herramientas, salida, pruebas. The root agents README documents the four product routes plus optional optimizer and distinguishes DeepAgents from deterministic LangGraph nodes.

- [ ] **Step 5: Replace stale product docs and update commands/paths**

`docs/arquitectura-agentes.md` uses a Mermaid dependency/flow diagram and links to colocated READMEs. `docs/better-harness.md` begins by saying it is optional and uses `optimization/fin.toml` in every live command.

- [ ] **Step 6: Verify docs and CLI without an LLM call**

Run: `uv run pytest tests/cli/test_agents.py tests/compat/test_documented_commands.py tests/test_no_sre_product.py -q`

Run: `uv run fin-graph agents list`

Run: `uv run fin-graph agents describe analysis.compatibility`

Expected: tests PASS; commands show supervisors, exactly 19 personas, tools/middleware/children and no duplicate IDs.

- [ ] **Step 7: Commit**

```bash
git add src/fin_graph/cli src/fin_graph/agents src/fin_graph/workflows src/fin_graph/runtime src/fin_graph/optimization optimization README.md docs tests
git commit -m "docs(agents): make multi-agent architecture navigable"
```

### Task 10: Ejecutar la aceptación completa sin cambios de comportamiento

**Files:**
- Modify only if evidence changed: `docs/compatibility/parity-report.md`

**Interfaces:**
- Consumes: all target packages and unchanged public surfaces.
- Produces: verified branch; no new runtime API.

- [ ] **Step 1: Run static quality and dependency checks**

Run: `uv run ruff check src tests`

Run: `uv run mypy`

Run: `uv run lint-imports`

Expected: all exit 0.

- [ ] **Step 2: Run the complete automated suite**

Run: `uv run pytest -q`

Expected: zero failures; only the previously documented environment-dependent skips.

- [ ] **Step 3: Re-run compatibility and SQLite acceptance**

Run: `uv run pytest tests/compat tests/acceptance -q`

Run: `uv run python scripts/acceptance_ai_hedge_fund.py --backend sqlite --output artifacts/acceptance-multi-agent-refactor`

Expected: 39/39 compatibility contracts, 14/14 SQLite checks and `public_digest` exactly `a9036a91c42b90abde555aec416592908ce6e3da95266d13410332cc3f983f9b`.

- [ ] **Step 4: Run scripted eval and optional harness validation**

Run: `uv run fin-graph eval run --suite smoke --model scripted --output-dir .runs/multi-agent-refactor-scripted`

Run: `uv run --extra harness fin-graph harness validate optimization/fin.toml`

Expected: eval 3/3 and harness config valid; base app/eval commands do not require `--extra harness`.

- [ ] **Step 5: Re-run the real local model smoke requested for this migration**

Run: `uv run fin-graph eval run --suite smoke --model ollama:llama3.2:3b --output-dir .runs/multi-agent-refactor-ollama`

Expected: 3/3 completed with recorded latency/tokens and no fallback/error; if Ollama is unavailable, record the concrete external precondition instead of substituting scripted evidence.

- [ ] **Step 6: Inspect the final diff for stale paths and accidental behavior edits**

Run: `git diff --check`

Run: `rg -n "fin_graph\.graphs|fin_graph\.harness|harness/fin\.toml" src tests README.md docs/arquitectura-agentes.md docs/better-harness.md examples langgraph.json`

Expected: clean diff; no live old paths. Historical specs/progress outside this search scope remain immutable records.

- [ ] **Step 7: Commit any evidence-only update**

```bash
git add docs/compatibility/parity-report.md
git commit -m "test: record multi-agent refactor acceptance"
```

Skip this commit when the digest and published evidence remain unchanged.
