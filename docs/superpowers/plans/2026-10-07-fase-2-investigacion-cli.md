# Fase 2 — grafo de investigación y comando investigate

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** convertir alertas en reportes RCA citados mediante un grafo LangGraph con especialistas paralelos, refinamiento acotado y un comando local `sre-graph investigate` que muestre progreso.

**Architecture:** El estado del grafo contiene solo datos JSON; clientes, modelos y credenciales viven fuera de él, en el contexto de ejecución o en dependencias de construcción. `Send` lanza un especialista por tarea y los reducers reúnen evidencia y hallazgos. Las etapas deterministas usan el dominio existente; planner, especialistas, síntesis y juicio de claims usan agentes LangChain con salida estructurada e inyección para pruebas offline.

**Tech Stack:** Python 3.12, LangGraph 1.2, LangChain 1.4, Typer, Rich, Pydantic, pytest, uv.

**Spec:** `docs/specs/2026-10-07-sre-graph-design.md`, secciones “Agentes”, “Grafo de investigación”, “CLI” y tabla de Fase 2.

## Global Constraints

- Rama normal `fase-2` creada desde `fase-1d` en el directorio principal. Sin worktree nuevo, push, despliegue ni infraestructura real; conservar `.idea/`.
- Estado serializable, sin secretos, clientes ni modelos. `RunContext` se reconstruye al invocar y nunca entra en checkpoints.
- Máximo 3 olas, 5 especialistas por ola, 8 llamadas a tools y 10 llamadas al modelo por especialista; deadline y límite de recursión configurables.
- Integraciones solo configuradas y tools de lectura por dominio; MCP y mutantes no entran automáticamente. Un fallo de especialista produce `degraded_finding` y no aborta el reporte.
- Entrega solo a destinos explícitos. El CLI no envía mensajes por defecto. Remediación se propone, nunca se ejecuta en esta fase.
- Credenciales/modelo reales y `eks/001` no están disponibles sin servicios externos: documentar como bloqueados, no como aprobados.
- Gate: pytest completo con Postgres local, Ruff, formato, mypy e import-linter.

## Decisiones y ajustes al diseño

- La memoria semántica, runbooks y deduplicación durable de entregas pertenecen a Fases 4–5. `gather_context` publica solo disponibilidad de integraciones en 2; una futura fuente de contexto se conecta por interfaz, sin fingir recall.
- En ausencia de integraciones o LLM válido, el grafo produce `unknown` con razones explícitas. No inventa evidencia.
- El juez LLM solo puede degradar un claim que pasó los chequeos deterministas; nunca mejora un claim sin evidencia.
- El listado GitHub deployments se filtra por página en 1D y conserva `nextPage`; los especialistas deben seguir la página cuando la primera no cubre la ventana.
- Studio exporta el grafo de investigación; el grafo demo se retira. El CLI local no incluye chat/REPL, servidor, agenda ni aprobación, que tienen fases posteriores.

## Review Focus

1. Alerta trivial o `user_requested=False`: no invocar modelos/tools ni entregar, `test_noise_short_circuit`.
2. Dos tareas concurrentes: exactamente dos `Send`, evidencia sin pérdida ni duplicado, `test_fanout_and_reducers`.
3. Modelo/especialista fallido: reporte `unknown` o degradado sin filtrar excepción/secretos, `test_specialist_failure_isolated`.
4. Verificación insuficiente: refinar como máximo 3 olas; sin evidencia ensanchar ventana a 7 días máximo, `test_refine_budget`.
5. Una tool mutante o MCP externa: no llega al especialista; entrega solo con destino explícito, `test_read_only_routing_and_delivery`.

## Mapa de archivos

| Ruta | Responsabilidad |
|---|---|
| `graphs/investigation/state.py` | Estado JSON y reducers de evidencia/hallazgos/errores |
| `graphs/investigation/services.py` | Interfaces inyectables de razonamiento y reloj; adaptación a agentes LangChain |
| `graphs/investigation/specialists.py` | Siete specs, filtrado de tools, agente por dominio y extracción de evidencia |
| `graphs/investigation/graph.py` | Nodos, `Send`, rutas, límites, eventos y fábrica compilada |
| `graphs/investigation/runtime.py` | Construcción de modelos/registry/delivery para CLI y Studio |
| `cli/main.py` | Typer `investigate --file|--text` y progreso Rich |
| `README.md`, `langgraph.json`, `docs/progress/` | Uso y evidencia del cierre |

### Task 1: estado y contratos de especialistas

**Interfaces:** `InvestigationState(TypedDict)` con `raw_alert`, `alert`, `incident_window`, `integrations`, `plan`, `tasks`, `findings`, `evidence`, `diagnosis`, `verification`, `wave`, `budget`, `report`, `deliveries`, `errors`, `destinations`; `merge_findings` por `(wave, domain)`; `SpecialistSpec` por los siete dominios; `available_specialists(registry, context)` y `read_tools(domain)`.

- [ ] Escribir tests de estado JSON, reducers y routing de las siete specs; `external`/`mutating` excluidas. Correr RED.
- [ ] Implementar estado/specs y pasar tests focalizados.
- [ ] Ruff, mypy, tests; commit `feat(investigation): define state and specialist routing`.

### Task 2: agentes de razonamiento y captura de evidencia

**Interfaces:** `InvestigationServices` con métodos async `extract`, `plan`, `investigate`, `synthesize`, `judge`; `LangChainServices` usa `make_agent`, `ToolStrategy` y `ModelRole` configurables. El especialista recibe solo tools de su dominio, `RunContext`, alerta/ventana/tarea delimitadas como datos, límites y middleware de guardrails; transforma `ToolMessage` y llamadas en `EvidenceItem` sin secretos.

- [ ] Escribir tests con `ScriptedChatModel` para extracción estructurada, una tool real offline, evidencia citada, límites y fallo seguro. Correr RED.
- [ ] Implementar adapters; pasar tests focalizados.
- [ ] Ruff, mypy, tests; commit `feat(investigation): add structured reasoning services`.

### Task 3: grafo completo hasta entrega

**Interfaces:** `make_investigation_graph(services, registry, delivery=None, limits=...)` compilado sin checkpointer; `StateGraph(..., context_schema=RunContext)`; `intake → gather_context → plan → Send×N specialist → synthesize → verify → refine (si procede) → propose_remediation → report → deliver → END`. `get_stream_writer()` emite etapas; entradas `raw_alert`, `user_requested`, `destinations`.

- [ ] Escribir tests offline para ruido, fan-out, reducers, fallback plan, fallo aislado, presupuesto/ventana, claims sin evidencia, reporte snapshot y entrega explícita. Correr RED.
- [ ] Implementar nodos y rutas deterministas; pasar tests focalizados.
- [ ] Ruff, mypy, tests; commit `feat(investigation): build bounded parallel RCA graph`.

### Task 4: CLI, Studio y cierre

**Interfaces:** `sre-graph investigate --file PATH|--text TEXT [--source SOURCE]` con progreso y Markdown final; `make_runtime_graph()` crea registry/modelos bajo demanda; `langgraph.json` publica investigación. Entrada inválida sale sin stacktrace ni secretos.

- [ ] Escribir tests CLI offline con grafo inyectado para flags excluyentes, progreso, salida Markdown y códigos de error. Correr RED.
- [ ] Añadir Typer/Rich y entry point, conectar fábrica local/Studio, README y registro de avance; pasar tests focalizados.
- [ ] Gate completo con Postgres; revisar diff y seguridad; revisión de código independiente y correcciones.
- [ ] Commit final. Mantener fase 2 local sin merge ni push salvo petición expresa.
