<!-- Estado: aprobado el 2026-10-07. Fuente: plan de diseño aprobado en Claude Code. -->

# Diseño: `sre-graph` — el sistema multiagente de incidencias de opensre, reconstruido nativo en LangGraph

## Contexto

- **Pedido:** replicar por completo, en LangGraph, el sistema multiagente de análisis de incidencias de
  opensre, en una versión óptima y usable, **sin usar el framework opensre**.
- **Por qué es una reconstrucción y no una migración:**
  - opensre usó LangGraph hasta may-2026 (`57a5cbe0c` "migrate off langgraph") y pasó a un loop ReAct propio.
  - El 2-sep-2026 (`ce77b4a0b`) borró el pipeline multiagente de investigación/RCA; hoy es un solo agente de chat.
  - Su CI prohíbe las palabras `langgraph`, `langchain` y `langsmith` (`tests/github_ci/test_removed_architecture_references.py`).
  - Por eso se crea un **repo nuevo**. opensre queda como especificación funcional y fuente de prompts,
    heurísticas, escenarios de evaluación y referencia de integraciones (Apache-2.0, con atribución).
- **Resultado esperado (v1):**
  - Un servicio Python que recibe alertas o preguntas y lanza especialistas en paralelo sobre
    K8s/Prometheus/Grafana/AWS/Datadog/Sentry/New Relic/GitHub.
  - Produce un RCA con evidencia citada y propone remediaciones que solo se ejecutan con aprobación humana.
  - Entrega por Slack/Telegram/PagerDuty.
  - Se usa desde API, CLI/REPL, bots, UI web y scheduler.
  - Trae un arnés de evaluación que mide la calidad del RCA.

## Decisiones cerradas

| Tema | Decisión |
|---|---|
| Base | Reimplementación nativa; **cero** dependencia de opensre |
| Lenguaje / servidor | Python 3.12 + FastAPI propio. `langgraph.json` solo para LangGraph Studio en dev (el servidor oficial exige licencia enterprise en producción self-hosted) |
| LLM | Modelos nativos LangChain (`init_chat_model`), proveedor por env |
| Alcance v1 | RCA por alerta, chat SRE, remediación HITL, arnés de evaluación, todas las superficies |
| Integraciones ola 1 | Kubernetes, Prometheus/Alertmanager, Grafana (Loki/Tempo/Mimir), AWS (CloudWatch, EKS, RDS, Lambda, CloudTrail), Datadog, Sentry, New Relic, GitHub. Entrega: Slack, Telegram, PagerDuty |
| Superficies | API REST + SSE, CLI/REPL, bots Slack/Telegram, UI web, scheduler |
| Logs y trazas | Nada persistido. Logging operativo a stdout (`LOG_LEVEL`). Sin LangSmith ni OTel (`LANGSMITH_TRACING=false` en `.env.example`). El estado de hilos se guarda solo para funcionar (reanudar, HITL, memoria), con retención |

**Supuestos (corregibles):**
- El repo es hermano de opensre: `C:\Users\USUARIO\Desktop\devops-telemetri\sre-graph`, con paquete `sre_graph` (nombre de trabajo).
- Single-tenant.
- Quedan fuera los extras de agente de código de opensre.

## Arquitectura objetivo

### Capas (dependencias solo hacia abajo, contrato con import-linter)

```
surfaces    server/ (FastAPI) · cli/ (Typer+Rich) · gateway/ (Slack, Telegram) · scheduler/ · evals/ · web/ (React)
graphs      investigation/ · remediation/ · chat/ · health_check/
agents      factory · subagents · specialists/ · middleware/ · testing/
capability  integrations/<vendor>/ · delivery/ · memory/ · runbooks/ · skills/
domain      domain/ (alertas, ventana, taxonomía, evidencia, diagnóstico, reporte) · safety/ (masking, guardrails, aprobación)
base        config/ (env + sre-graph.yaml no secreto) · llm/ (modelos por rol) · persistence/ (checkpointer, store, tablas)
```

Reglas transversales (lecciones de opensre y del starter kit):
- **Estado serializable.** El estado del grafo es un `TypedDict` con valores JSON-serializables. Los modelos Pydantic se usan solo en las fronteras de los nodos.
- **Nunca secretos en el estado ni en los args del modelo.**
  - Los clientes con credenciales viajan en el runtime context (`StateGraph(..., context_schema=RunContext)`) y las tools los leen vía `ToolRuntime.context`.
  - El context no se persiste: se reconstruye en cada invoke y en cada resume.
- **Un solo checkpointer.** Solo el grafo más externo lo lleva; subgrafos y subagentes lo heredan (así `interrupt()` burbujea).
- **`langgraph.json` sin checkpointer.** Exporta fábricas compiladas sin checkpointer (Studio aporta la persistencia). FastAPI compila los mismos grafos con Postgres.
- **Esqueleto determinista y hojas agénticas.** Intake, report y deliver son código; el razonamiento vive en planner, especialistas, sintetizador y verificador.
  - Descartado: un único agente ReAct con más de 100 tools (el diseño actual de opensre), por contexto inflado y falta de paralelismo.
  - Descartado: un supervisor 100% LLM, por costo y porque se evalúa peor.

### Agentes

| Agente | Rol | Implementación |
|---|---|---|
| Planner (comandante) | Hipótesis y tareas por especialista | LLM `reasoning`, salida `InvestigationPlan` |
| 7 especialistas | kubernetes · metrics · logs · traces · cloud · changes · errors | `create_agent` con las tools de su dominio (solo integraciones configuradas), `response_format=SpecialistFinding` |
| Sintetizador | Diagnóstico en formato incident-command | LLM `reasoning`, salida `Diagnosis` |
| Verificador | Valida claims contra la evidencia citada y decide si hay que refinar | Determinista + LLM `judge` |
| Operador de remediación | Prepara, pide aprobación, ejecuta y verifica | Subgrafo HITL |
| Copiloto SRE (chat) | Conversa, delega en especialistas y lanza investigaciones | `create_agent` supervisor (especialistas como tools; patrón subagents del starter kit) |

**Dominios → integraciones:**
- **kubernetes** → K8s.
- **metrics** → Prometheus/Mimir, CloudWatch Metrics, Datadog, NRQL.
- **logs** → Loki, CloudWatch Logs Insights, Datadog, logs de pods.
- **traces** → Tempo, Datadog APM, New Relic.
- **cloud** → EKS, RDS (+ Performance Insights), Lambda, alarms.
- **changes** → GitHub, rollout history, CloudTrail, releases de Sentry.
- **errors** → Sentry, errores APM de New Relic y Datadog.

Los especialistas se definen una vez en `agents/specialists/registry.py` (`SpecialistSpec`: descripción de ruteo, integraciones, prompt, límites). Se reutilizan en investigación y en chat, y se compilan en caché por (spec, set de integraciones, modelo).

### Grafo de investigación (`graphs/investigation`)

```
START → intake ──(ruido)──────────────────────────────────────────────────→ END
          └→ gather_context → plan ⇒ Send×N → specialist ─┐
                                ↑                          ▼
                              refine ←─(baja confianza y   synthesize → verify
                                        hay presupuesto)                 │ (ok)
                  propose_remediation ←──────────────────────────────────┘
                         → report → deliver ─(acciones + política)→ remediate (subgrafo) → END
```

| Nodo | Qué hace | Fuente a portar |
|---|---|---|
| `intake` | Parsers deterministas por fuente (Alertmanager, Grafana, Datadog, CloudWatch/SNS, Sentry, PagerDuty, New Relic, genérico) → `NormalizedAlert`. LLM `fast` solo para texto libre. Compuerta de ruido (`user_requested` la salta). Calcula la ventana del incidente | opensre `core/domain/alerts/*`, `core/domain/types/incident_window.py`, prompt de `ce77b4a0b^:tools/investigation/stages/intake/node.py` |
| `gather_context` | Integraciones disponibles (sin secretos), incidentes similares (Store semántico), runbooks por identidad exacta | `core/domain/runbooks/selection.py` |
| `plan` | 1–4 hipótesis con criterios para confirmar/descartar, más tareas por especialista. Post-filtro determinista (disponibilidad, máximo de paralelos). Fallback determinista si falla el LLM | `core/domain/alerts/alert_source.py`, `ce77b4a0b^:core/domain/alerts/tool_planning.py` |
| `specialist` (×N con `Send`) | Corre el subagente del dominio sobre su tarea y devuelve `SpecialistFinding` + `EvidenceItem`s vía reducers. Si falla, devuelve un hallazgo degradado y el grafo sigue | Reglas de `ce77b4a0b^:tools/investigation/stages/gather_evidence/{prompt,loop,tools}.py` |
| `synthesize` | `Diagnosis`: triage, bloque Status, hipótesis con veredicto, verificación, preguntas de seguimiento, trade-offs, causa raíz, categoría (taxonomía por fuente), cadena causal, claims validados con ids de evidencia, score | `.../gather_evidence/incident_command.py`, `ce77b4a0b^:core/domain/diagnosis/*` |
| `verify` | Chequeos deterministas: cada claim cita evidencia existente, secciones completas, regla "healthy". Juez LLM por claim; degrada los claims sin soporte. Decide `refine` o seguir | `57a5cbe0c^:app/nodes/root_cause_diagnosis/{claim_validator,evidence_checker}.py` |
| `refine` | Nueva ola dirigida a las hipótesis abiertas; ensancha la ventana si la evidencia vino vacía | `57a5cbe0c^:app/nodes/adapt_window/rules.py` |
| `propose_remediation` | Convierte pasos en `RemediationAction`s ejecutables (nivel de efecto, riesgo, rollback), ordenadas por radio de impacto y reversibilidad | `57a5cbe0c^:app/nodes/root_cause_diagnosis/remediation_templates.py` |
| `report` | Markdown, Slack Block Kit, HTML de Telegram y nota de PagerDuty. Guarda el resumen del RCA en la memoria (Store) | `ce77b4a0b^:tools/investigation/reporting/**` |
| `deliver` | Envía a los canales del trigger o de la severidad, con claves de idempotencia | — |

**Estado (`InvestigationState`) y sus reducers:**
- Entrada y contexto: `raw_alert`, `alert`, `is_noise`, `incident_window`, `integrations`, `recalled`, `runbooks`.
- Investigación: `hypotheses` (merge por id), `tasks`, `evidence` (append con dedupe), `findings` (append), `diagnosis`, `verification`, `wave`, `budget`.
- Remediación y entrega: `remediation`, `approvals`, `actions_executed`, `report`, `deliveries`.
- Soporte: `masking_map`, `usage` (tokens por hilo, para `/cost`), `errors`.

**Presupuestos por defecto (configurables):**
- Máximo 3 olas y 5 especialistas por ola.
- Por especialista: 8 tool calls y 10 llamadas al modelo.
- Deadline por corrida: al vencer, sintetiza con lo que tenga.
- `recursion_limit` calculado y `RetryPolicy` en los nodos con I/O.

### Grafo de remediación (`graphs/remediation`) — HITL

Pasos:
1. **`prepare`:** revalida las precondiciones y hace dry-run server-side en K8s.
2. **`approve`:** `interrupt()` con el esquema estándar de `HumanInTheLoopMiddleware` (`action_requests` → `decisions` approve/edit/reject). Es **un formato único** de aprobación para chat, remediación, UI, CLI y bots. El aprobador debe estar en la allowlist; se verifica en la superficie y otra vez en el nodo.
3. **`execute`:** cada acción va en `@task`, para que una reanudación no la repita, y lleva clave de idempotencia.
4. **`verify_fix`:** re-evalúa la expresión de la alerta original o la salud del workload, con polling acotado.
5. **`report_outcome`:** si la verificación falla, propone un rollback y vuelve a `approve`.

Reglas:
- No hay efectos secundarios antes de `interrupt()`, porque el nodo se re-ejecuta al reanudar.
- `read_only` nunca interrumpe. `mutating`/`external` siempre interrumpe, salvo allowlist por entorno; `prod` fuerza aprobación.
- Los args editados se re-validan y pasan otra vez por dry-run.
- Las aprobaciones vencidas las rechaza un job del scheduler.

### Chat SRE (`graphs/chat`)

**Tools:**
- Especialistas como tools (`ask_<dominio>`).
- `investigate`: corre el grafo de investigación como subgrafo, con progreso en streaming.
- `search_incident_memory` y `save_note`.
- `load_runbook`: solo fuentes confiables y match exacto; el runbook es guía, no orden.
- `load_skill`: playbooks markdown con divulgación progresiva.
- Las tools mutantes pasan por `HumanInTheLoopMiddleware`.

**Middleware:**
- De LangChain: `SummarizationMiddleware`, `ContextEditingMiddleware`, `TodoListMiddleware`, `ModelRetryMiddleware`, `ModelFallbackMiddleware`, `ToolRetryMiddleware`, `ModelCallLimitMiddleware`.
- Propios: `MaskingMiddleware`, `GuardrailsMiddleware` y `EvidenceGateMiddleware` (si se afirma una causa raíz sin evidencia en el turno, un nudge, como el goal-review de opensre).

**Memoria:** el hilo vive en el checkpointer. El largo plazo vive en el Store: `("org","incidents")`, `("org","notes")` y `("users",id,"prefs")`.

**Fuentes para los prompts:** persona y reglas de `core/agent_harness/prompts/opensre_system_prompt.md`; playbook de runbooks de `core/agent_harness/prompts/skills/investigating-incidents-with-runbooks/SKILL.md` (solo lectura, se reescribe).

### Chequeos programados (`graphs/health_check`)

1. **`run_checks`:** consultas deterministas, sin LLM.
2. **`assess`:** umbrales primero, luego LLM `fast`.
3. Si hay anomalía, marca `escalate`; el scheduler, no el grafo, abre una investigación en un hilo propio.
4. **`digest`:** resumen tipo morning briefing.

### Integraciones (`integrations/<vendor>/`)

Cada paquete tiene:
- `settings.py`: pydantic-settings con prefijo.
- `client.py`: async; los SDK síncronos corren con `asyncio.to_thread`.
- `tools.py`: tools LangChain con metadata (`integration`, `domains`, `evidence_type`, `side_effect_level`, `idempotent`).
- `verify.py`.

Además:
- **`IntegrationRegistry`:** registro explícito, sin import mágico. Filtra por integraciones configuradas y sanas, y por dominio.
- **Salida compacta:** las tools devuelven JSON compacto (top-N, truncado) más un `evidence_id`; el payload completo, con tope, va al colector de evidencia.
- **Middleware de especialistas:** `EvidenceCaptureMiddleware` y `ToolResultCacheMiddleware` (mismo tool + args en la corrida → resultado cacheado; es la regla de opensre de no repetir una llamada).

| Vendor | Tools de lectura (v1) | Mutantes (con aprobación) |
|---|---|---|
| kubernetes | Pods, detalle de pod, logs (previous/tail/since), events, workloads con condiciones, rollout history, nodes, HPA, uso (metrics.k8s.io) | Rollout restart/undo, scale, delete pod |
| prometheus/mimir + alertmanager | query, query_range, labels/series, alerts, rules, targets, silences | Create/expire silence |
| grafana | Loki query_range/labels, Tempo search/trace, alert rules, annotations, dashboards | Create annotation |
| aws | CloudWatch Logs Insights, GetMetricData, alarms; EKS; RDS instance/events/Performance Insights; Lambda; CloudTrail | — (solo lectura en v1) |
| datadog | Logs, metrics, monitors, events, APM spans, incidents | Mute monitor |
| sentry | Issues, detalle con stacktrace, eventos, releases | Resolve/ignore |
| newrelic | NRQL (NerdGraph), issues e incidentes, entidades, errores APM | — |
| github | Deployments, commits y PRs en la ventana, workflow runs, compare | Create issue |
| entrega | Slack, Telegram, nota en PagerDuty | Ack/resolve en PagerDuty |
| mcp | Loader genérico de `mcp-servers.json` (langchain-mcp-adapters); dominio y nivel de efecto por config, `external` por defecto | Según config |

Referencia funcional (endpoints, queries, edge cases): opensre `integrations/{kubernetes,alertmanager,grafana,tempo,cloudwatch,eks,rds,aws_lambda,cloudtrail,datadog,sentry,new_relic,github,slack,telegram,pagerduty}/`. Se reescriben; no se importan.

### Seguridad (`safety/`)

- **Masking reversible antes del modelo:**
  - Cubre pod, namespace, cluster, hostname, account_id, IP, email, service y regex extra.
  - Desenmascara la salida del modelo y los args de las tools; el mapa por hilo vive en el estado.
  - Apagado por defecto (`MASK_ENABLED`). Se porta de `infrastructure/safety/masking/*`.
- **Guardrails:** redacta secretos (keys AWS, tokens, llaves privadas, credenciales en URLs) en las salidas de tools y en los mensajes salientes. Se porta de `infrastructure/safety/guardrails/rules.py`.
- **Aprobación:** política por `side_effect_level` y allowlists de aprobadores por superficie.
- **Inyección de prompts:** las salidas de tools, los runbooks y las alertas se delimitan como datos, y los prompts llevan reglas anti-inyección.
- **API:** API keys con roles (viewer/operator/approver/admin); webhooks verificados por fuente (token, HMAC, firma SNS); CORS para la UI.

### Persistencia y memoria (`persistence/`, `memory/`)

- **Checkpointer:** InMemory (tests), SQLite (CLI local), Postgres (servidor), con `setup()`.
- **Store:**
  - InMemory en dev; PostgresStore + pgvector (`init_embeddings`) en el servidor.
  - `EMBEDDINGS_PROVIDER` va aparte, porque Anthropic no tiene embeddings (igual que en el starter kit). Sin embeddings, la búsqueda cae a metadatos.
- **Tablas propias** (SQLAlchemy 2 async + Alembic): `runs`, `alert_fingerprints` (dedupe), `schedules`, `api_keys`, `deliveries` (idempotencia).
- **Retención:** un job borra hilos (`delete_thread`) y filas pasados `THREAD_RETENTION_DAYS` (30 por defecto). Fuera de los hilos no se guardan prompts ni transcripts.

### Superficies

**API (`server/`):**
- El lifespan compila los grafos una vez (Postgres) y arranca scheduler, Slack y MCP.
- `RunManager`:
  - ejecuta tareas async con un semáforo (`MAX_CONCURRENT_RUNS`) y un lock por hilo;
  - guarda el estado en `runs`;
  - reconstruye el context al reanudar;
  - marca las corridas colgadas como `interrupted` para poder reanudarlas.
- Rutas `/v1`:
  - Investigaciones e hilos: `POST /investigations`, `GET /investigations/{thread}`, `GET /threads`, `GET /threads/{id}/history`.
  - Streaming y aprobación: `GET /threads/{thread}/stream` (SSE), `POST /threads/{thread}/resume`.
  - Chat: `POST /chat/threads/{thread}/messages` (SSE).
  - Alertas: `POST /alerts/{source}`, con 8 fuentes y dedupe por fingerprint.
  - Gestión: `/schedules`, `/integrations` (+ verify), `/health`, `/ready`.
- Sirve la UI estática.

**CLI (`cli/`):**
- Comandos: `serve`, `ask`, `chat`, `investigate --file|--text`, `integrations list|verify`, `schedules`, `threads`, `eval`.
- El REPL de `chat` usa prompt_toolkit + Rich, con streaming y los comandos `/new /threads /resume /investigate /approve /integrations /cost /exit`.
- Modos: local (in-process, SQLite en `platformdirs`) o remoto (`--server` + API key).

**Gateway (`gateway/`):**
- Slack con slack-bolt:
  - Socket Mode en dev; HTTP montado en FastAPI en prod.
  - Ack en menos de 3 s y el trabajo en background.
  - `thread_ts` ↔ `thread_id`.
  - Listener del canal de alertas, mensaje de progreso que se edita, reporte en Block Kit y botones de aprobación que reanudan el grafo.
- Telegram por webhook, con inline keyboard.

**Scheduler (`scheduler/`):**
- APScheduler con job store en Postgres e instancia única (advisory lock).
- Jobs: health_check, digest, retención y vencimiento de aprobaciones.

**UI (`web/`):**
- React + Vite + TS + Tailwind.
- Pantallas: investigaciones (lista y detalle con timeline en vivo, hipótesis, evidencia, reporte y tarjeta de aprobación), chat, schedules e integraciones.
- Streaming con fetch (permite enviar el header de API key). El build va dentro de la imagen Docker.

### Contrato de eventos (único para CLI, UI y bots)

`ProgressEvent{type: stage|specialist|tool|hypothesis|token|interrupt|report|error|done, thread_id, run_id, ts, data}`.
- Los nodos emiten con `get_stream_writer()`.
- Un adaptador traduce `stream_mode=["updates","custom","messages"]` (con `subgraphs=True`) a este contrato.

### LLM (`llm/`)

- `get_model(role)` con cuatro roles: `reasoning` (planner y sintetizador), `worker` (especialistas), `fast` (intake, ruido, assess) y `judge` (verify y eval).
- Se crean con `init_chat_model("<provider>:<model>")`.
- Variables de entorno: `LLM_PROVIDER`, `LLM_MODEL_<ROL>`, `LLM_FALLBACK_MODELS`.
- Tabla de defaults por proveedor. Para Anthropic: `claude-sonnet-5-5` en reasoning/worker/judge y `claude-haiku-4-5-20251001` en fast. Los IDs se revisan al implementar.
- Prompt caching con el middleware de langchain-anthropic cuando el proveedor es Anthropic.
- El `usage_metadata` se agrega por hilo para `/cost`.

### Evaluación (`evals/`)

- **Dataset inicial:** 36 escenarios de opensre recuperados de git, `ce77b4a0b^:tests/synthetic/{eks (14), rds_postgres (21), rds_upstream (1)}`. Cada uno trae:
  - `alert.json` y fixtures por fuente;
  - `scenario.yml`, con herencia `base`;
  - `answer.yml`: categoría, keywords y fuentes requeridas, keywords de descarte, categorías prohibidas, trayectoria óptima, máximo de olas y respuesta de referencia.
- **`FixtureBackend`:** clientes falsos por vendor que sirven las fixtures, así las tools y el grafo corren sin cambios. Incluye una tabla de mapeo de nombres de tools y fuentes de opensre a sre-graph.
- **Scorers:**
  - categoría (con las prohibidas), keywords y keywords de descarte;
  - fuentes de evidencia usadas y validez de las citas;
  - trayectoria (cobertura, extras, olas);
  - juez LLM contra la referencia (port de `ce77b4a0b^:integrations/opensre/llm_eval_judge.py` y `.../rds_postgres/scoring.py`);
  - tokens y latencia.
- **Runner:** `sre-graph eval run --suite eks --model ... --repeat 3` genera JSON + Markdown en local y compara corridas.

## Qué se porta y de dónde (referencia; se reescribe)

| Pieza | Fuente |
|---|---|
| Kit de agentes: `make_agent`, `subagent_tool`/`make_supervisor`, resolvedor de persistencia, validación de requests, modelo guionado para tests | langgraph-starter-kit `src/agents/{factory,supervisor,persistence}.ts`, `src/config/checkpointer.ts`, `src/server/validation.ts`, `tests/helpers/scripted-model.ts`, `tests/agents/*.test.ts`, `docs/BUILDING.md` |
| Topología del grafo original (Send, loop diagnose↔adapt_window) | opensre `57a5cbe0c^:app/pipeline/{graph,routing}.py` |
| Etapas del pipeline final (orden, hooks, degradación) | `ce77b4a0b^:tools/investigation/{agent_pipeline,capability}.py` y `stages/**` |
| Patrón Python StateGraph con loop buscar→validar→reintentar | systems-agentic-workflow `agents/langgraph_agents/discovery_agent/discovery_flow.py` |
| Dominio, seguridad, integraciones, evaluación | Rutas indicadas en cada sección |

Atribución: `NOTICE` (Apache-2.0) para los prompts, heurísticas y escenarios de opensre y para los patrones del starter kit.

## Fases (v1 = Fases 0–7; cada fase recibe su plan TDD detallado antes de implementarse)

| # | Fase (tamaño) | Entregable | Aceptación |
|---|---|---|---|
| 0 | Fundaciones (M) | Repo uv, settings, `llm/`, `persistence/`, kit de agentes portado, `ScriptedChatModel`, esqueletos de middleware, `langgraph.json`, Docker Compose (Postgres+pgvector), CI (ruff, mypy, pytest, import-linter). **Spike de mecanismos críticos:** `Send` con subagentes `create_agent`, `interrupt` en subgrafo + resume, `@task` dentro de un nodo, runtime context al reanudar, middleware propio con `wrap_tool_call` | Tests portados del starter kit (delegación, interrupt/resume, salida estructurada) en verde y offline; `langgraph dev` abre Studio; `docker compose up` sano; el spike documenta cada mecanismo (si `@task` en nodo no sirve: un nodo por acción + idempotencia) |
| 1 | Dominio, seguridad e integraciones ola 1 (XL) | `domain/`, `safety/`, `IntegrationRegistry`, runtime context, 8 vendors de lectura, entrega, loader MCP, `verify` | Tests por vendor con respuestas grabadas (respx/moto/stub de K8s); round-trip de masking; `integrations verify` contra endpoints reales si existen |
| 2 | Grafo de investigación + CLI mínimo (L) | Grafo completo hasta `deliver` (remediación solo como propuesta), especialistas, `sre-graph investigate` con progreso | Tests offline: el ruido corta; fan-out = n.º de tareas; merge de reducers; `refine` para por olas/presupuesto; degradación ante fallo de especialista; snapshot del reporte. `eks/001` con modelo real → categoría correcta |
| 3 | Arnés de evaluación (M) | 36 escenarios portados, FixtureBackend, scorers, runner y reporte | `eval run --suite smoke` (guionado) en CI; `--suite eks` y `rds` con modelo real → línea base documentada |
| 4 | API FastAPI + webhooks (L) | RunManager, rutas `/v1`, SSE, auth, 8 webhooks con verificación y dedupe, retención, despliegue Docker | Tests httpx/ASGI + SSE; POST de un payload de Alertmanager → reporte consultable; reinicio a mitad de corrida → reanudable |

**Ajuste de implementación de fase 4 (2026-10-07):** el listado de superficies
describe la API final. En fase 4 quedan operativas investigación, hilos, SSE,
reanudación, webhooks, integraciones y health/ready. Chat/HITL se implementa en fase
5, schedules y gateways en fase 6 y UI estática en fase 7. El servidor compila el
grafo una vez y usa Postgres; scheduler, Slack listener y MCP no arrancan todavía.
CloudWatch SNS verifica firma RSA-SHA256 con clave pública fijada por el operador,
TopicArn y SigningCertURL exactos; se rechazan confirmaciones y nunca se descarga
un certificado indicado por el payload. Los demás webhooks usan token o HMAC por
fuente, con un proxy de entrada para vendors que no permiten añadir el header.
| 5 | Remediación HITL + chat + memoria + REPL (L) | Subgrafo de remediación, tools mutantes, política de aprobación, copiloto SRE, Store de incidentes, runbooks y skills, `sre-graph chat` | interrupt → approve/edit/reject; crash-replay sin doble ejecución; HITL en tool mutante desde el chat; recall de un incidente similar |
| 6 | Bots Slack/Telegram + scheduler (M) | Gateway con aprobaciones, listener de alertas, jobs y grafo `health_check` | Tests con Web API mockeada y tiempo congelado; prueba manual en un workspace sandbox |
| 7 | UI web (M) | SPA con investigaciones en vivo, aprobaciones, chat, schedules e integraciones | Vitest + Playwright: escenario con FixtureBackend → aprobar una acción desde la UI |
| 8 | Endurecimiento y olas 2+ (continuo) | Ver detalle debajo de la tabla | Paridad de integraciones con opensre; eval sin regresión |

Detalle de la Fase 8:
- Revisión de seguridad y prueba de carga concurrente.
- Multi-réplica: fan-out de SSE con Postgres LISTEN/NOTIFY y líder único de scheduler.
- Wizard `integrations setup` y grabador de escenarios a partir de incidentes reales.
- Olas 2+ de integraciones, nativas o vía MCP: Opsgenie, incident.io, Jira, Honeycomb, Elastic/OpenSearch, Splunk, VictoriaLogs, Coralogix, Azure, ArgoCD, Helm, GitLab…

## Verificación end-to-end (al cerrar v1)

1. `uv run pytest`: unit, integraciones grabadas, grafos con modelo guionado y API.
2. `uv run sre-graph eval run --suite all --model anthropic:claude-sonnet-5-5`: acierto de categoría y scores contra la línea base de la Fase 3.
3. Demo en vivo:
   1. Cluster `kind` + kube-prometheus-stack + Loki + una app de demo con fallo inyectado (OOM o imagen inválida).
   2. Alertmanager llama a `POST /v1/alerts/alertmanager`.
   3. La investigación se ve en vivo en la UI y el CLI, y el reporte llega a Slack.
   4. Se aprueba `rollout undo` con el botón y `verify_fix` confirma la recuperación.
4. `curl` del flujo API (investigación, stream SSE, resume) y Playwright sobre la UI.
5. Reiniciar el servidor durante una investigación y durante una aprobación pendiente: ambas se reanudan sin repetir acciones.

## Fuera de alcance v1

- Monitoreo de agentes de código (fleet).
- Reparación de CI, merge conflicts y alertas de seguridad de GitHub.
- Sandbox de ejecución Python y work items.
- `/goal` multi-turno (se evalúa en la Fase 8 con TodoList + continuación).
- Multi-tenant y binario PyInstaller.

## Riesgos y salvaguardas

- **Costo y latencia multiagente** → presupuestos por nivel, modelo `fast` donde alcance, caché de resultados de tools y contexto aislado por especialista.
- **Doble ejecución al reanudar** → sin efectos antes de `interrupt()`, `@task` + idempotencia, y test de crash-replay.
- **Contexto inflado** → JSON compacto, colector de evidencia y `ContextEditingMiddleware`.
- **Tormentas de alertas** → dedupe por fingerprint y semáforo de concurrencia.
- **Deriva de APIs de LangChain/LangGraph 1.2** → versiones fijadas en `uv.lock` y tests de contrato sobre middleware y tools.

## Siguientes pasos al aprobar

1. Crear el repo `sre-graph` y guardar este diseño como `docs/specs/2026-10-07-sre-graph-design.md`.
2. Escribir con writing-plans el plan de implementación detallado (tareas TDD) de las Fases 0–2 y elegir el modo de ejecución.
3. Implementar la Fase 0 (incluido el spike).
