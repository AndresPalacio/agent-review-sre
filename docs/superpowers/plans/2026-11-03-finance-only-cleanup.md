# Finance-Only Repository Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminar del árbol actual toda superficie operacional SRE/AWS y dejar únicamente el producto financiero, sus evaluaciones y la infraestructura transversal que necesita.

**Architecture:** La limpieza avanza desde los límites distribuibles hacia el runtime: primero retira datos y artefactos empaquetados, luego estrecha contratos tipados y persistencia, después convierte ejemplos y pruebas, elimina UI/documentación histórica y termina con una frontera finance-only ejecutable. Cada tarea deja su subconjunto en verde antes de continuar.

**Tech Stack:** Python 3.12, Pydantic, LangGraph/DeepAgents, FastAPI, SQLAlchemy/Alembic, React 19/TypeScript/Vite/Vitest, Pytest, Ruff, MyPy, import-linter.

**Spec:** `docs/superpowers/specs/2026-11-03-finance-only-cleanup-design.md`

## Global Constraints

- Conservar análisis multiagente, chat, cartera paper, órdenes con aprobación, backtesting, scheduler de mercado, harness financiero y API compatible con la aplicación financiera de referencia.
- Conservar `src/fin_graph/evals/data/equities/` y sus 16 escenarios.
- Conservar Slack y Telegram sólo como destinos de informes y alertas financieras.
- Conservar detección de credenciales cloud dentro de `safety/secrets.py`; retirar detectores de identificadores operacionales que ya no pertenecen al producto.
- Las menciones a AWS dentro de noticias congeladas de compañías son evidencia financiera legítima.
- No modificar ni eliminar `.fin-graph/` ni detener la aplicación que corre en el puerto 8000.
- Las bases SRE antiguas dejan de ser compatibles; las migraciones se optimizan para instalaciones financieras nuevas.
- Ejecutar Python directamente desde `.venv` con `UV_NO_SYNC=1` para no intentar reemplazar el ejecutable bloqueado por la aplicación activa.

## Review Focus

- Un wheel nuevo no debe incluir el manifiesto SRE obsoleto ni fixtures RDS/EC2; Task 1 inspecciona los nombres internos del wheel.
- Estrechar `EvidenceType` no debe romper metadata de herramientas financieras ni MCP financiero; Task 2 prueba el conjunto exacto y los adaptadores.
- Reescribir la cadena Alembic para instalaciones nuevas no debe dejar tablas con nombres anteriores ni romper upgrade repetible; Task 3 valida el esquema final.
- Los ejemplos async deben seguir demostrando supervisor, hijos y MCP, pero sólo con `fundamentals` y `technicals`; Task 4 ejecuta sus escenarios deterministas.
- La eliminación documental no debe romper enlaces ni comandos públicos actuales; Task 6 amplía las pruebas de documentación ejecutable.

---

### Task 1: Retirar artefactos y datasets operacionales distribuibles

**Files:**
- Modify: `tests/evals/market/test_equity_evals.py`
- Modify: `tests/packaging/test_clean_install.py`
- Modify: `.gitignore`
- Delete: `src/fin_graph/evals/data/manifest.json`
- Delete: `src/fin_graph/evals/data/rds_upstream/`
- Delete: `projects/sre-better-harness/`
- Delete: `projects/sre-deepagents/`

**Interfaces:**
- Consumes: `load_equity_suite("all") -> list[EquityScenario]` y el layout actual del wheel.
- Produces: `src/fin_graph/evals/data/` con una única raíz `equities/`; ningún resultado generado bajo `projects/` se distribuye ni versiona.

- [ ] **Step 1: Escribir las pruebas fallidas del corpus y del wheel**

En `test_equity_evals.py`, añadir `test_packaged_eval_data_contains_only_equities()` y afirmar que los hijos de `src/fin_graph/evals/data` son exactamente `{"equities"}`. En `test_clean_install.py`, afirmar que ningún miembro del wheel contiene `/rds_`, `/eks/`, `cloudwatch`, `sre-` o `evals/data/manifest.json`, sin examinar el texto de noticias financieras.

- [ ] **Step 2: Ejecutar las pruebas y confirmar RED**

Run: `$env:UV_NO_SYNC='1'; .\.venv\Scripts\python.exe -m pytest tests/evals/market/test_equity_evals.py::test_packaged_eval_data_contains_only_equities tests/packaging/test_clean_install.py::test_wheel_contains_app_static_and_every_compatibility_namespace -q`

Expected: FAIL mostrando `manifest.json`, `rds_upstream` y sus miembros dentro del wheel.

- [ ] **Step 3: Eliminar únicamente los objetivos inventariados**

Eliminar los dos árboles `projects/sre-*`, los once fixtures RDS/EC2 y el manifiesto de 276 entradas obsoletas. Añadir `/projects/` a `.gitignore`; no tocar `.eval-results/`, `.harness-runs/` ni el corpus `equities/`.

- [ ] **Step 4: Verificar corpus, harness y wheel**

Run: `$env:UV_NO_SYNC='1'; .\.venv\Scripts\python.exe -m pytest tests/evals/market/test_equity_evals.py tests/optimization/harness/test_dataset_quality.py tests/packaging/test_clean_install.py -q`

Expected: PASS; 16 escenarios financieros y manifest del harness v2 válido.

- [ ] **Step 5: Commit**

```powershell
git add -A .gitignore src/fin_graph/evals/data projects tests/evals/market/test_equity_evals.py tests/packaging/test_clean_install.py
git commit -m "refactor: remove operational evaluation artifacts"
```

### Task 2: Cerrar contratos de evidencia y masking al dominio financiero

**Files:**
- Modify: `src/fin_graph/domain/tools.py`
- Modify: `src/fin_graph/domain/thesis_verification.py`
- Modify: `src/fin_graph/integrations/analytics/tools.py`
- Modify: `src/fin_graph/integrations/mcp/config.py`
- Modify: `src/fin_graph/integrations/payload.py`
- Modify: `src/fin_graph/safety/masking.py`
- Modify: `mcp-servers.example.json`
- Modify: `tests/domain/test_tools.py`
- Modify: `tests/domain/test_evidence.py`
- Modify: `tests/domain/finance_helpers.py`
- Modify: `tests/agents/chat/test_chat_middleware.py`
- Modify: `tests/agents/common/middleware/test_approval.py`
- Modify: `tests/agents/common/middleware/test_tool_cache.py`
- Modify: `tests/integrations/test_mcp_loader.py`
- Modify: `tests/integrations/test_mcp_runtime.py`
- Modify: `tests/integrations/test_registry.py`
- Modify: `tests/integrations/test_tooling.py`
- Modify: `tests/config/test_settings.py` (sólo `MASK_KINDS` en esta tarea)
- Modify: `tests/safety/test_approval.py`
- Modify: `tests/safety/test_masking.py`
- Modify: `tests/safety/test_secrets.py` (datos de ejemplo, no los detectores de credenciales)

**Interfaces:**
- Produces: `EvidenceType` exactamente con `prices`, `financials`, `news`, `insider`, `valuation_model`, `indicators` y `positions`.
- Produces: `IdentifierKind` sin `pod`, `namespace`, `cluster`, `hostname`, `account_id` ni `service_name`; conserva PII financiera, email e IP.
- Produces: `McpToolPolicy.evidence_type` con default `EvidenceType.FINANCIALS`.

- [ ] **Step 1: Escribir contratos fallidos de conjuntos exactos**

Añadir pruebas que comparen `{item.value for item in EvidenceType}` y `{item.value for item in IdentifierKind}` con los conjuntos financieros descritos en Interfaces. Reescribir fixtures MCP/integración y `MASK_KINDS` para usar `prices`, `financials`, `news`, `positions`, `portfolio_id` o `email`, nunca logs/pager/pods.

- [ ] **Step 2: Ejecutar las pruebas y confirmar RED**

Run: `$env:UV_NO_SYNC='1'; .\.venv\Scripts\python.exe -m pytest tests/domain/test_tools.py tests/safety/test_masking.py tests/integrations/test_mcp_runtime.py tests/integrations/test_registry.py tests/integrations/test_tooling.py -q`

Expected: FAIL por tipos operacionales todavía presentes y fixtures que aún usan sus nombres.

- [ ] **Step 3: Reducir enums y adaptar consumidores financieros**

Cambiar usos financieros de `EvidenceType.METRICS` a `EvidenceType.FINANCIALS`; cambiar el default MCP de `EVENTS` a `FINANCIALS`; retirar los detectores y claves estructuradas operacionales de `masking.py`. Reescribir `mcp-servers.example.json` como servidor `financial_research` con herramienta `search_filings`, dominio `fundamentals`, evidencia `financials` y variable `MCP_FINANCE_AUTH`.

- [ ] **Step 4: Verificar metadata, MCP, middleware y masking**

Run: `$env:UV_NO_SYNC='1'; .\.venv\Scripts\python.exe -m pytest tests/domain/test_tools.py tests/domain/test_evidence.py tests/agents/common/middleware tests/agents/chat/test_chat_middleware.py tests/integrations tests/safety/test_masking.py tests/safety/test_secrets.py tests/safety/test_approval.py -q`

Expected: PASS; todas las tools conservan metadata financiera y los detectores de credenciales de `safety/secrets.py` permanecen intactos.

- [ ] **Step 5: Commit**

```powershell
git add src/fin_graph/domain src/fin_graph/integrations src/fin_graph/safety/masking.py mcp-servers.example.json tests/domain tests/agents tests/integrations tests/safety
git commit -m "refactor: constrain tool contracts to finance"
```

### Task 3: Retirar ventanas, namespaces y migraciones heredadas

**Files:**
- Modify: `src/fin_graph/domain/window.py`
- Modify: `src/fin_graph/domain/thesis_categories.py`
- Modify: `src/fin_graph/server/app.py`
- Modify: `src/fin_graph/server/storage.py`
- Modify: `src/fin_graph/server/migrations/versions/0001_server_ledger.py`
- Modify: `src/fin_graph/server/migrations/versions/0003_checkpoint_threads.py`
- Modify: `src/fin_graph/server/migrations/versions/0006_phase6.py`
- Delete: `src/fin_graph/server/migrations/versions/0012_finance_names.py`
- Modify: `src/fin_graph/server/migrations/versions/0013_compatibility_store.py`
- Modify: `tests/domain/test_window.py`
- Modify: `tests/server/test_checkpoint_namespace.py`
- Modify: `tests/server/test_storage.py`
- Modify: `tests/server/test_migration.py`

**Interfaces:**
- Produces: `AnalysisWindow(as_of: date, lookback_days: int = 365)` como único modelo de ventana del módulo.
- Produces: `checkpoint_thread_ids(thread_id: str) -> tuple[str, str, str]` con análisis, `chat:<id>` y `orders:<id>`.
- Produces: una cadena Alembic nueva `0011_paper_ledger -> 0013_compatibility_store`; las tablas nacen como `signal_fingerprints` y `watch_results`.

- [ ] **Step 1: Escribir pruebas fallidas de namespaces y esquema limpio**

Añadir `test_checkpoint_thread_ids_are_financial()` con igualdad exacta a `(thread_id, f"chat:{thread_id}", f"orders:{thread_id}")`. En `test_migration.py`, afirmar que el esquema final contiene `signal_fingerprints` y `watch_results` y no contiene `alert_fingerprints` ni `health_results`.

- [ ] **Step 2: Ejecutar las pruebas y confirmar RED**

Run: `$env:UV_NO_SYNC='1'; .\.venv\Scripts\python.exe -m pytest tests/domain/test_window.py tests/server/test_checkpoint_namespace.py tests/server/test_storage.py tests/server/test_migration.py -q`

Expected: FAIL por el cuarto namespace y por los nombres intermedios heredados.

- [ ] **Step 3: Simplificar runtime y rebasar las migraciones para instalaciones nuevas**

Eliminar `IncidentWindow`, `WindowSource`, `resolve_window` y constantes de minutos. Implementar `checkpoint_thread_ids()` una sola vez en `storage.py` y reutilizarla desde `app.py` y `delete_thread()`. Crear directamente `signal_fingerprints` en 0001 y `watch_results` en 0006; borrar 0012 y apuntar `0013.down_revision` a `0011_paper_ledger`. Quitar comentarios comparativos de incidentes.

- [ ] **Step 4: Verificar dominio, retención y cadena Alembic**

Run: `$env:UV_NO_SYNC='1'; .\.venv\Scripts\python.exe -m pytest tests/domain/test_window.py tests/server/test_checkpoint_namespace.py tests/server/test_storage.py tests/server/test_phase6_storage.py tests/server/test_migration.py tests/architecture/test_reference_boundary.py -q`

Expected: PASS; upgrade repetible desde una base vacía y retención limitada a hilos financieros.

- [ ] **Step 5: Commit**

```powershell
git add -A src/fin_graph/domain/window.py src/fin_graph/domain/thesis_categories.py src/fin_graph/server tests/domain/test_window.py tests/server
git commit -m "refactor: remove legacy operational persistence"
```

### Task 4: Convertir pruebas, workflows y demos genéricos a finanzas

**Files:**
- Modify: `tests/cli/test_chat.py`
- Modify: `tests/config/test_settings.py`
- Modify: `tests/conftest.py`
- Modify: `tests/persistence/test_paths.py`
- Modify: `tests/spikes/test_send_fanout.py`
- Modify: `tests/spikes/test_subgraph_node_interrupt.py`
- Modify: `tests/work/test_service.py`
- Modify: `packages/workflow/examples/solver_cases.py`
- Modify: `packages/workflow/examples/solver_benchmark.py`
- Modify: `packages/workflow/tests/test_solver_evaluation.py`
- Modify: `packages/workflow/README.md`
- Modify: `examples/demo_supervisor_async.py`
- Modify: `examples/demo_mcp_async.py`
- Modify: `examples/async_pilot.html`
- Modify: `examples/README.md`
- Modify: `.run/Fin Graph - Orquestador offline.run.xml`

**Interfaces:**
- Produces: benchmark family `research` que descubre un manifiesto financiero y genera `artifacts/watchlist.json`.
- Produces: demo async que delega exclusivamente a `fundamentals` y `technicals` para analizar tickers.

- [ ] **Step 1: Escribir/actualizar expectativas financieras antes del código de ejemplo**

Cambiar URLs a `finance.example`, DBs a `fin_graph`, IDs a `analysis-*`, los spikes a `AnalysisState` y `OrderApprovalState`, y las aserciones de `test_solver_evaluation.py` de `investigation/pages` a `research/watchlist`.

- [ ] **Step 2: Ejecutar pruebas y confirmar RED**

Run: `$env:UV_NO_SYNC='1'; .\.venv\Scripts\python.exe -m pytest tests/cli/test_chat.py tests/config/test_settings.py tests/persistence/test_paths.py tests/spikes tests/work/test_service.py packages/workflow/tests -q`

Expected: FAIL en expectativas renombradas mientras los ejemplos todavía generan conceptos operacionales.

- [ ] **Step 3: Reescribir ejemplos preservando el mecanismo demostrado**

Mantener cancelación, actualización de hijo, idempotencia HTTP/MCP y fan-out. Sustituir métricas/logs/pagos por AAPL o MSFT, fundamentales/técnicos, watchlists y aprobación de orden paper. Actualizar textos, IDs DOM, localStorage y nombres visibles del piloto HTML.

- [ ] **Step 4: Verificar tests y demos deterministas**

Run: `$env:UV_NO_SYNC='1'; .\.venv\Scripts\python.exe -m pytest tests/cli/test_chat.py tests/config/test_settings.py tests/persistence/test_paths.py tests/spikes tests/work packages/workflow/tests -q`

Run: `$env:UV_NO_SYNC='1'; .\.venv\Scripts\python.exe examples/demo_supervisor_async.py --scenario lifecycle`

Expected: PASS y salida final que nombre `fundamentals` y `technicals`.

- [ ] **Step 5: Commit**

```powershell
git add tests/cli tests/config tests/conftest.py tests/persistence tests/spikes tests/work packages/workflow examples .run
git commit -m "refactor: make examples finance specific"
```

### Task 5: Eliminar la interfaz React de incidentes no conectada

**Files:**
- Delete: `web/src/InvestigationContent.tsx`
- Delete: `web/src/InvestigationContent.css`
- Delete: `web/src/InvestigationContent.test.tsx`
- Modify: `web/src/App.test.tsx`

**Interfaces:**
- Produces: bundle web limitado a flujos financieros, ejecución multiagente, explicación, cartera, órdenes y backtests.

- [ ] **Step 1: Añadir una regresión de superficie financiera**

En `App.test.tsx`, afirmar que la navegación renderizada ofrece estrategias, análisis, cartera, órdenes y backtests y nunca muestra “causa raíz”, “incidente” ni “remediación”.

- [ ] **Step 2: Ejecutar Vitest y confirmar el estado previo**

Run: `npm test -- --run App.test.tsx` (workdir `web`)

Expected: la nueva aserción financiera pasa; el paso demuestra que `InvestigationContent` no tiene consumidor productivo antes de borrarlo.

- [ ] **Step 3: Eliminar los tres archivos huérfanos**

No sustituirlos con un wrapper. La explicación financiera existente vive en `components/native/AnalysisExplainView.tsx` y el output multiagente en `components/output/`.

- [ ] **Step 4: Verificar tests, typecheck y bundle**

Run: `npm test` (workdir `web`)

Run: `npm run build` (workdir `web`)

Expected: PASS; ningún chunk contiene los textos de remediación retirados.

- [ ] **Step 5: Commit**

```powershell
git add -A web/src
git commit -m "refactor: remove unused incident interface"
```

### Task 6: Podar documentación, scripts y metadata históricos

**Files:**
- Delete: `docs/arquitecture-review/`
- Delete: `docs/progress/`
- Delete: `docs/specs/2026-10-07-fin-graph-design.md`
- Delete: `docs/integrations-wave2.md`
- Delete: `docs/phase8-acceptance.md`
- Delete: `docs/phase8-sandbox.md`
- Delete: `docs/compatibility/sre-removal-inventory.md`
- Delete: `docs/superpowers/plans/2026-10-*.md`
- Delete: completed plans `docs/superpowers/plans/2026-11-02-*.md` and `docs/superpowers/plans/2026-11-03-ai-hedge-fund-*.md`
- Delete: `docs/superpowers/plans/2026-11-03-multi-agent-code-organization.md`
- Delete: `docs/superpowers/plans/2026-11-03-solver-reliability.md`
- Delete: `docs/superpowers/specs/2026-10-*.md`
- Delete: `scripts/phase8_acceptance.py`
- Delete: `.idea/`
- Modify: `README.md`
- Modify: `docs/compatibility/parity-report.md`
- Modify: `docs/superpowers/specs/2026-11-03-ai-hedge-fund-drop-in-replacement-design.md`
- Modify: `docs/superpowers/specs/2026-11-03-multi-agent-code-organization-design.md`
- Modify: `tests/compat/test_documented_commands.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: documentación durable centrada en `README.md`, `docs/arquitectura-agentes.md`, `docs/better-harness.md`, `docs/compatibility/` y `docs/operations/`.
- Produces: este plan y su spec como únicos documentos de ejecución activos de la limpieza.

- [ ] **Step 1: Ampliar la prueba de documentación durable**

Añadir `docs/arquitectura-agentes.md` y `docs/better-harness.md` a `DOCS`; afirmar que README y esos documentos no enlazan rutas eliminadas (`progress/`, `arquitecture-review/`, `integrations-wave2`, `phase8`) y que cada enlace Markdown local resuelto existe.

- [ ] **Step 2: Ejecutar y confirmar RED**

Run: `$env:UV_NO_SYNC='1'; .\.venv\Scripts\python.exe -m pytest tests/compat/test_documented_commands.py -q`

Expected: FAIL por enlaces y documentos históricos todavía presentes/referenciados.

- [ ] **Step 3: Eliminar historia obsoleta y corregir documentación conservada**

Eliminar sólo los grupos enumerados. Reescribir referencias históricas necesarias en specs financieras con lenguaje de producto actual; no crear `legacy/` ni `archive/`. Añadir `.idea/` a `.gitignore`. Mantener este diseño y este plan porque documentan la decisión vigente.

- [ ] **Step 4: Verificar documentación y comandos**

Run: `$env:UV_NO_SYNC='1'; .\.venv\Scripts\python.exe -m pytest tests/compat/test_documented_commands.py tests/compat/test_compat_settings.py -q`

Expected: PASS; todos los enlaces locales y comandos públicos conservados resuelven.

- [ ] **Step 5: Commit**

```powershell
git add -A .idea .gitignore README.md docs scripts/phase8_acceptance.py tests/compat/test_documented_commands.py
git commit -m "docs: remove nonfinancial product history"
```

### Task 7: Instalar la frontera finance-only y ejecutar aceptación completa

**Files:**
- Create: `tests/architecture/test_finance_only_product.py`
- Delete: `tests/test_no_sre_product.py`
- Modify if needed: `pyproject.toml`
- Modify if needed: `uv.lock`

**Interfaces:**
- Produces: una prueba autocontenida que protege rutas, dependencias, comandos, imports, enums y assets web financieros sin depender de bases históricas.

- [ ] **Step 1: Escribir la frontera final**

La prueba debe afirmar: ausencia de rutas operacionales inventariadas; dependencias sin `boto3`, `botocore` ni SDK Kubernetes; conjunto exacto de `EvidenceType`; CLI sin comandos de investigación/remediación; ausencia de `InvestigationContent`; importabilidad de agentes, runtime, trading, harness y servidor; y ausencia de `projects/`. Las únicas excepciones textuales admitidas son `safety/secrets.py`, noticias de `evals/data/equities/` y los documentos de decisión de esta limpieza.

- [ ] **Step 2: Ejecutar la frontera y corregir sólo hallazgos reales**

Run: `$env:UV_NO_SYNC='1'; .\.venv\Scripts\python.exe -m pytest tests/architecture/test_finance_only_product.py -q`

Expected: PASS. Si falla, clasificar cada coincidencia: eliminar funcionalidad operacional; conservar sólo seguridad o evidencia financiera explícitamente permitida.

- [ ] **Step 3: Ejecutar búsquedas de auditoría**

Run: `git grep -In -E "AWS|CloudWatch|EKS|RDS|Kubernetes|PagerDuty|Datadog|Grafana|Prometheus|Alertmanager|remediation|incident|SRE" -- . ':!docs/superpowers/specs/2026-11-03-finance-only-cleanup-design.md' ':!docs/superpowers/plans/2026-11-03-finance-only-cleanup.md' ':!src/fin_graph/safety/secrets.py' ':!src/fin_graph/evals/data/equities/**' ':!tests/architecture/test_finance_only_product.py'`

Expected: ninguna capacidad operacional; cualquier palabra en noticias, seguridad o contratos financieros se documenta en la allowlist mínima.

- [ ] **Step 4: Ejecutar todas las verificaciones**

Run: `$env:UV_NO_SYNC='1'; .\.venv\Scripts\python.exe -m pytest -q`

Run: `.\.venv\Scripts\ruff.exe check src tests evals packages/workflow`

Run: `.\.venv\Scripts\mypy.exe`

Run: `.\.venv\Scripts\lint-imports.exe`

Run: `npm test` y `npm run build` (workdir `web`)

Run: `git diff --check; git status --short`

Expected: suite completa verde, build web verde, 16 escenarios financieros, arquitectura limpia y sólo cambios intencionales.

- [ ] **Step 5: Commit**

```powershell
git add -A
git commit -m "test: enforce finance-only product boundary"
```

- [ ] **Step 6: Revisión final independiente**

Solicitar revisión del diff completo con foco en pérdidas de capacidades financieras, migración limpia desde cero, wheel, ejemplos multiagente y allowlists demasiado amplias. Corregir issues Critical/Important y repetir Step 4 antes de declarar terminado.
