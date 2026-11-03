# Plan de implementación

Spec: `docs/superpowers/specs/2026-11-02-workflow-library-design.md`.

1. Crear paquete independiente y tests de API. Implementar `definition.py`, `runtime.py`,
   `compiler.py`, `registry.py` y `testing.py`. Validar input antes de ejecutar;
   identidad incluye contratos, políticas y revisiones. Solo LangGraph ejecuta la topología.
2. Implementar `patterns.py` y `adapters.py`: fan-out acotado, copias aisladas, orden de map,
   subflows, tools/modelos/agentes inyectables y extras perezosos. Tests offline.
3. Implementar `persistence.py` y `effects.py`: CAS y transiciones, memoria/SQLite,
   scope por namespace, admisión separada, replay confirmado y bloqueo de incertidumbre.
4. Documentar API, límites y diferencias frente a la referencia; ejemplo ejecutable,
   CI independiente, wheel e instalación mínima.
5. Revisar implementación completa; corregir hallazgos con regresiones; ejecutar
   suite de paquete, lint, mypy, ejemplo, build y suite existente.

## Interfaces compartidas

`StepNode(id, handler, *, input_schema=None, output_schema=None, artifact_version='1',
kind='deterministic', policy=ExecutionPolicy(), metadata=...)` recibe/devuelve dict.
`step(id, handler, **kwargs)` y `typed_step(id, handler, input_schema, output_schema,
artifact_version)`; el segundo valida modelos. Handlers pueden ser sync/async.
`runtime.call_handler(handler, value)` aísla el input y soporta ambas modalidades.
`runtime.execute_step(node, state)` aplica contratos/políticas/fixtures al nodo.
`CompiledWorkflow.definition`, `.definition_digest`, `.graph`; `run_workflow` y
`arun_workflow(compiled, input_data, *, context=None)` -> `RunResult`.
`RunResult.status` COMPLETE/FAILED/TIMED_OUT/CANCELLED, `.output` BaseModel|None,
`.cause` SafeCause|None, `.context` RunContext; `RunContext(run_id, ... )` y
`current_context()` para propagación. `WorkflowRegistry.resolve(name, version)`,
`.run(name=..., version=..., input_data=...)` y `.arun(...)`.

## Riesgos a revisar

- Un handler sync en thread no puede matarse al vencer timeout; nunca reintentar timeout.
- Input Pydantic construido sin validar debe revalidarse.
- Branches/map no deben ampliar ni perder contexto o saltarse interceptores.
- No reclamar idempotencia externa por disponer de un ledger local.
- SQLite debe reclamar ownership atómicamente incluso con dos conexiones.

## Progreso

- Inspección: repo limpio, rama `feat/fin-graph`; no existe SDK genérico.
- Entorno: `uv sync --locked --extra agents` preparado; suite base y final verificadas.
- Decisión: trabajar en la rama existente sin operaciones Git ni publicación.
- Implementados núcleo, composición, adaptadores inyectados, memoria/SQLite y efectos.
- Revisión independiente aplicada: revalidación anidada, alias/Json, salida no JSON,
  cancelación espontánea, persistencia de fechas estrictas y preflight sync/async.
- 98 pruebas del paquete pasan en Python 3.12 y 3.14; este último muestra 13 avisos
  de deprecación del LangSmith instalado (asyncio.iscoroutinefunction), sin fallos.
- Ruff check/format y mypy strict pasan (11 módulos de producción).
- Wheel final construido e instalado sin dependencias opcionales en entorno limpio.
  Smoke fuera del repo comprueba alias, Json, SQLite/reapertura y ausencia de
  fin_graph/FastAPI/DeepAgents. Artefacto: dist/workflow/bm_workflow-0.1.0-py3-none-any.whl.
- Suite existente: 1505 passed, 24 skipped por Postgres/Ollama/harness no configurados.
- LangSmith declarado directamente porque Dry Run importa tracing_context; no añade
  servicio obligatorio ni instalación nueva respecto de las transitivas de LangGraph.
