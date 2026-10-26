# Fase 5: remediación HITL, chat y memoria — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir investigar, recordar y conversar sobre incidentes, y ejecutar una remediación únicamente después de una decisión humana verificable.

**Architecture:** La investigación conserva su flujo de solo lectura y guarda un resumen en el Store. Un subgrafo independiente recibe una acción explícita, la prepara, interrumpe para aprobarla, ejecuta con clave de idempotencia y verifica el resultado. Un agente de chat con checkpointer expone especialistas y herramientas de memoria y usa el mismo contrato de aprobación para toda herramienta mutante. API y CLI son superficies del mismo flujo, con contexto sin secretos en el estado.

**Tech Stack:** Python 3.12, LangGraph 1.2, LangChain 1.4, FastAPI, Typer, Postgres/SQLite/InMemory Store, pytest.

**Spec:** `docs/specs/2026-10-07-fin-graph-design.md` (Fase 5, secciones de remediación, chat, memoria y superficies).

## Global Constraints

- Trabajar en la rama normal `fase-5`; no usar worktrees nuevos, push ni despliegue.
- No invocar infraestructura real en la validación; clientes simulados y Postgres local solamente.
- `read_only` no requiere aprobación; `mutating` y `external` sí, salvo allowlist no productiva; producción siempre obliga a aprobar.
- La decisión es `{"decisions":[{"type":"approve|edit|reject", ...}]}`; solo roles approver/admin pueden aprobar en API.
- Cada edición de argumentos se vuelve a validar y preparar; no hay mutación antes del `interrupt`.
- Una clave estable de idempotencia y `@task` protegen la reanudación; si el destino no confirma idempotencia, no se reintenta automáticamente tras resultado incierto.
- Los secretos y prompts/transcripts quedan fuera del Store de largo plazo.

## Review Focus

- Reanudación duplicada o concurrente: una decisión se aplica una vez, bajo lock de hilo.
- Aprobación falsificada: el actor proviene de la API/contexto, no del cuerpo de la decisión.
- Edición de herramienta o destino: rechazar cambios fuera del catálogo y repetir dry-run.
- Fallo entre llamada externa y checkpoint: documentar y probar el estado incierto sin repetir una mutación no idempotente.
- Runbook o skill malicioso: solo identificadores confiables y contenido delimitado como datos.

---

### Task 1: Modelo y catálogo de acciones mutantes

**Files:** `src/fin_graph/domain/remediation.py`, `src/fin_graph/integrations/*/{client,tools}.py`, `tests/domain/test_remediation.py`, `tests/integrations/test_mutations.py`.

**Interfaces:** `RemediationAction` existente; `ActionCatalog.prepare(action, context) -> PreparedAction`; `ActionCatalog.execute(prepared, idempotency_key, context) -> ActionResult`; herramientas con `ToolMeta.side_effect_level` explícito.

- [ ] Añadir pruebas offline de validación de herramienta, argumentos y operaciones K8s/Alertmanager/Datadog/Sentry/GitHub/Grafana/PagerDuty según la tabla del diseño.
- [ ] Ejecutar las pruebas focalizadas y confirmar fallo.
- [ ] Implementar los métodos de clientes, dry-run/validación, metadata y registro explícito.
- [ ] Ejecutar pruebas focalizadas, Ruff y mypy; corregir hasta verde.
- [ ] Commit de la unidad.

### Task 2: Subgrafo HITL durable y superficie de aprobación

**Files:** `src/fin_graph/graphs/remediation/{__init__,graph,state}.py`, `src/fin_graph/server/{manager,app}.py`, `tests/graphs/remediation/test_graph.py`, `tests/server/test_approvals.py`.

**Interfaces:** `make_remediation_graph(catalog, policy, checkpointer)`; `RunManager.approve(thread_id, decision, actor)`; `POST /v1/threads/{thread_id}/approve`.

- [ ] Probar approve/edit/reject, allowlist, prod, replay, verificación y fallo/rollback, más controles de rol y concurrencia de API.
- [ ] Confirmar fallos iniciales.
- [ ] Implementar prepare → approve → execute → verify_fix → report_outcome; conectar ejecución explícita al hilo sin auto ejecutar propuestas de RCA.
- [ ] Exponer interrupciones en eventos/estado y reanudar con `Command(resume=...)` tras autenticar actor.
- [ ] Ejecutar pruebas focalizadas, Ruff y mypy; commit.

### Task 3: Memoria y fuentes confiables

**Files:** `src/fin_graph/memory/{__init__,service}.py`, `src/fin_graph/runbooks/{__init__,catalog}.py`, `src/fin_graph/skills/{__init__,catalog}.py`, `src/fin_graph/graphs/investigation/graph.py`, `tests/memory/test_service.py`, `tests/graphs/investigation/test_memory.py`.

**Interfaces:** `IncidentMemory.save_incident(report)`, `search_incidents(query, limit)`, `save_note(text)`, `load_runbook(id)`, `load_skill(id)`; namespaces del diseño.

- [ ] Probar resumen sin transcript, recall por metadatos sin embeddings, selección exacta de runbook/skill y entrada no confiable.
- [ ] Confirmar fallo inicial y construir Store con fallback y colecciones confiables.
- [ ] Guardar el RCA verificado desde report; pruebas focalizadas, Ruff y mypy; commit.

### Task 4: Agente de chat con HITL

**Files:** `src/fin_graph/graphs/chat/{__init__,graph,tools,prompts}.py`, `src/fin_graph/agents/middleware/*`, `tests/graphs/chat/test_chat.py`.

**Interfaces:** `make_chat_graph(model, registry, memory, investigation, checkpointer, store, policy)`; herramientas `ask_<domain>`, `investigate`, `search_incident_memory`, `save_note`, `load_runbook`, `load_skill`; middleware LangChain y propios.

- [ ] Probar hilo multi-turno offline, delegación, investigación, recall, evidencia y tool mutante interrumpida.
- [ ] Confirmar fallos iniciales y construir el supervisor con límites y protección de datos.
- [ ] Ejecutar pruebas focalizadas, Ruff, mypy e import-linter; commit.

### Task 5: API SSE y CLI REPL

**Files:** `src/fin_graph/server/{app,manager}.py`, `src/fin_graph/cli/{main,chat}.py`, `pyproject.toml`, `tests/server/test_chat_api.py`, `tests/cli/test_chat.py`.

**Interfaces:** `POST /v1/chat/threads/{thread_id}/messages` SSE; `fin-graph ask`, `fin-graph chat` con `/new /threads /resume /investigate /approve /integrations /cost /exit`.

- [ ] Probar autenticación, streaming, interrupción, reanudación y comandos offline.
- [ ] Confirmar fallos iniciales; implementar API y REPL con persistencia SQLite local o servidor configurado.
- [ ] Ejecutar pruebas focalizadas, Ruff y mypy; commit.

### Task 6: Gate, documentación y cierre

**Files:** `README.md`, `.env.example`, registro `.superpowers/sdd/.../progress.md`, diseño si hay ajustes.

- [ ] Ejecutar pytest completo con Postgres local si está disponible, Ruff check/format, mypy, import-linter y evaluaciones smoke.
- [ ] Revisar diff contra el diseño y corregir hallazgos; documentar limitaciones externas/credenciales.
- [ ] Actualizar README y registro con evidencia, commit, integrar localmente en `main` mediante merge no destructivo y verificar estado final.
