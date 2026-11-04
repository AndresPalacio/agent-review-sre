# Resultado: fiabilidad y evaluación de bm-workflow

Fecha: 3 de noviembre de 2026. Paquete: `bm-workflow` 0.2.0. Implementación del
[diseño de fiabilidad](../specs/2026-11-03-solver-reliability-design.md), con DeepAgents 0.7.32
y dependencias sin cambios. Se conservaron los cambios anteriores y la rama existente.

## Qué cambió y qué lo demuestra

| Problema reproducido | Corrección | Evidencia |
| --- | --- | --- |
| Una propuesta estructurada en el mismo turno que una acción omitía la acción. | Ejecutar las herramientas pendientes mediante las rutas nativas antes de verificar; reparar también desde after_agent. | Regresiones de efecto único, rechazo y reparación, cuotas agotadas; revisión independiente con nueve combinaciones de cuota/reparaciones. |
| ProblemSpec/limits construidos sin validación podían saltarse contratos. | Revalidar y aislar datos en las fronteras públicas. | Construcción/copia inválida rechazada antes de invocar el grafo; contenedores anidados congelados. |
| El verificador podía alterar la propuesta devuelta. | Pasar una copia validada de los datos, manteniendo identidad de clientes y contexto. | Mutación anidada con alias/Json no altera la salida ni la segunda comprobación. |
| Una terminación textual sin propuesta no permitía reconocer requisitos faltantes. | Consultar al verificador con candidate=None; nunca aprobar sin propuesta. | Bloqueo autoritativo y aprobación sin propuesta convertida en inconclusive. |
| El resumen personalizado perdía el prompt y recuperación por exceso de contexto nativos. | Modelo de resumen con reservas alrededor del motor nativo. | Una reserva impide el segundo intento; dos permiten la recuperación con historial reducido; contabilidad y callbacks sin duplicación. |
| El especialista predeterminado no heredaba skills; una herramienta del host llamada task ocultaba la delegación. | Herencia acotada solo para el especialista automático y reserva del nombre task. | Pruebas con delegación real, carga lenta cancelada, aislamiento de especialistas explícitos y formatos de herramienta soportados. |
| Evaluación limitada y métricas incomparables/incompletas. | Research financiero multifuente, variantes reservadas, verificación externa simétrica, recuperación por efectos observados, tokens parciales explícitos y modelos nuevos por brazo. | 22 pruebas de evaluación, incluidas fuentes ocultas inaccesibles para el modelo y errores posteriores al resumen. |

Los mecanismos de compresión y recuperación se reutilizan del
[código oficial de DeepAgents 0.7.32](https://github.com/langchain-ai/deepagents/blob/deepagents==0.7.32/libs/deepagents/deepagents/middleware/summarization.py).
La herencia de skills sigue el comportamiento del
[especialista predeterminado nativo](https://docs.langchain.com/oss/python/deepagents/subagents#skills-inheritance).
Se revisaron también [middleware de LangChain](https://docs.langchain.com/oss/python/langchain/middleware/built-in)
y [persistencia de LangGraph](https://docs.langchain.com/oss/python/langgraph/persistence);
no se añadieron selección de herramientas mediante otro modelo, memoria ni recuperación
durable sin evidencia que justificara esas capacidades y su coste.

## Evaluación ejecutada

Los 14 casos por brazo usan modelos guionizados y herramientas locales reales. Son
contratos de funcionamiento, no una comparación de razonamiento con proveedores.
La nueva familia descubre rutas en un manifiesto y cruza inventario de servicios,
observaciones y política para escribir un artefacto de escalamiento. Su variante
reservada cambia rutas, datos, orden, umbral y resultado. Otro caso omite una fuente.

| Medida | Baseline LangChain | Solver |
| --- | ---: | ---: |
| Casos públicos resueltos | 5/11 | 6/11 |
| Variantes reservadas resueltas | 3/3 | 3/3 |
| Total resuelto por evaluador independiente | 8/14 | 9/14 |
| Falsos éxitos | 2 | 0 |
| Bloqueos | 3 | 3 |
| Recuperaciones observadas | 1 | 2 |
| Efectos duplicados / excedentes de cuota / errores de ejecución | 0 / 0 / 0 | 0 / 0 / 0 |
| Llamadas de modelo / herramientas / resumen | 43 / 30 / 0 | 47 / 31 / 0 |
| Tokens totales | Desconocidos | Desconocidos |
| Duración agregada observada, construcción incluida | 0.149 s | 0.825 s |

Una medición local por caso no acredita ventajas estadísticas de velocidad. Las
variantes reservadas son públicas en el repositorio, no casos ciegos de generalización.
La evidencia falsa y el diagnóstico equivocado se inyectan en el modelo guionizado;
las filas con factory de proveedor lo identifican como perturbación no inyectada.

El informe JSON separa `by_split`, `verified_resolution_rate` (estado aprobado y éxito
declarado, igual para ambos brazos), `host_verified_resolution_rate` (aprobación interna
del solver), `total_model_calls`, `observed_tokens` y la completitud de tokens.
Los subtotales nunca se presentan como consumo completo después de una llamada sin uso.

## Verificación final

- Python 3.12: **195 passed** en 10.54 s.
- Python 3.14: **195 passed** en 7.23 s; 13 avisos de deprecación de LangSmith sobre
  `asyncio.iscoroutinefunction`, sin fallos.
- Proyecto principal: **1505 passed, 24 skipped** en 97.20 s. Se omiten Postgres,
  Ollama y better_harness no configurados; no se ocultaron fallos.
- Ruff lint y formato: aprobados, 33 archivos; mypy estricto: 16 módulos aprobados.
- Quickstart, reparación offline, delegación y benchmark: salida correcta, código 0.
- Revisión independiente: sin hallazgos P1/P2 pendientes; 53 pruebas focalizadas del
  runtime/resumen, 22 de evaluación y la matriz de nueve combinaciones aprobadas.
- Wheel nuevo: `dist/workflow-reliability/bm_workflow-0.2.0-py3-none-any.whl`. Instalado
  en un entorno Python 3.12 nuevo con dependencias del lock y sin extras. Ejecución
  con `-I` desde fuera del repositorio: flujo tipado, validación e importación opcional
  aprobados; fin_graph, FastAPI y DeepAgents ausentes; el constructor sin extra ofrece
  un error explícito. El wheel contiene el nuevo módulo de resumen.
- `git diff --check` aprobado. No hubo commits, publicación, despliegue ni descarte.

## Cómo usarlo

La API `build_solver_agent`/`solver_step` se conserva. Los verificadores que puedan
diagnosticar falta de capacidades deben aceptar `candidate=None` y devolver `blocked`
con los requisitos ausentes. La [documentación del paquete](../../../packages/workflow/README.md)
incluye instalación, composición, errores recuperables y semántica de métricas.

Desde la raíz del repositorio:

```console
uv run --project packages/workflow --locked --extra agents python packages/workflow/examples/solver_benchmark.py
uv run --project packages/workflow --locked --extra agents python packages/workflow/examples/solver_benchmark.py --case investigate-holdout
```

Una evaluación con proveedor requiere el opt-in explícito `--model-factory modulo:funcion`,
una instancia nueva por caso/brazo, credenciales y un presupuesto decidido por el host.
No se realizaron llamadas de proveedor en esta sesión.

## Decisiones y límites conservados

- Se trabajó en el checkout existente y se mantuvo todo sin commit, porque allí estaba
  la implementación anterior autorizada. Los informes de trabajo se conservan.
- Se continuó autónomamente con tareas delegadas de propiedad separada, como solicitó
  el usuario, sin repetir la implementación terminada ni pedir otra aprobación del diseño.
- Se mantuvo DeepAgents 0.7.32: las extensiones necesarias ya existen en esa versión.
- Los errores arbitrarios de herramientas siguen siendo terminales: reintentarlos
  podría duplicar efectos inciertos. `ToolException` con manejo habilitado por el host
  permite decisiones posteriores del modelo; no introduce retries automáticos.
- Las cuotas de llamadas de agente/herramientas coinciden en ambos brazos. El solver
  permite además cuatro resúmenes, no usados en estos 14 casos. Se optó por explicitar
  esa diferencia, sin otro motor de cuotas; una comparación real de eficiencia debe
  igualar el presupuesto total de inferencia, tokens o coste.
- Sigue pendiente demostrar calidad con modelos reales y casos ciegos. No había
  credenciales de proveedor en el entorno ni presupuesto autorizado para hacerlo.
- La cancelación es cooperativa; no detiene forzosamente threads ni revierte efectos
  externos. El host conserva la responsabilidad sobre verificadores, permisos e
  idempotencia. No se añade durable resume, HITL ni despliegue.

Los reportes detallados y JSON antes/después permanecen en
`.superpowers/sdd/2026-11-03-solver-reliability/`, incluido `independent-review.md`.
