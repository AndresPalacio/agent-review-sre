# Better-harness de producción para fin-graph

Esta integración utiliza el [better-harness oficial de LangChain](https://github.com/langchain-ai/deepagents/tree/be75459c444b53d21d633ef2ab997a01d0739c71/examples/better-harness),
fijado en `uv.lock`. El Deep Agent externo propone cambios al harness del agente
SRE existente. LangGraph conserva la investigación, especialistas, verificación,
seguridad y entrega del producto. El optimizador utiliza las clases, variantes,
workspace, proposer y reportes oficiales; el runner SRE añade procesos separados,
contratos de ejecución y protección emparejada de holdout.

## Ejecución predeterminada del producto

CLI (`investigate`, `ask`, `chat`), API/UI, webhooks, gateways, escalamiento de health
checks y Studio construyen las investigaciones mediante
[`harness/runtime.py`](../src/fin_graph/harness/runtime.py). Las evals y los replays
usan la misma fábrica. Los imports anteriores de `graphs.investigation` se conservan
como compatibilidad y delegan al harness.

```powershell
uv sync --locked
uv run fin-graph harness runtime
uv run fin-graph investigate --text 'Investiga los errores del servicio checkout'
uv run fin-graph serve
```

El runtime de incidentes forma parte de la instalación básica. No inicia campañas
de optimización ni importa `better_harness`/`deepagents`. El extra `harness` es necesario
sólo para los comandos de optimización y sus evaluaciones upstream.

La configuración se resuelve en `harness/profile.py`, desde el entorno o `.env`:

| Variable | Default | Rango |
|---|---:|---|
| `FIN_HARNESS_MAX_WAVES` | 3 | 1–3 |
| `FIN_HARNESS_MAX_TASKS_PER_WAVE` | 5 | 1–5 |
| `FIN_HARNESS_MAX_TOTAL_TASKS` | 15 | 1–15 |
| `FIN_HARNESS_DEADLINE_SECONDS` | 180 | 1–1800 |
| `FIN_HARNESS_EXPECTED_VARIANT` | sin pin | Huella `sha256:…` del código instalado |

La variante activa es la huella de las nueve superficies instaladas. `harness runtime`
muestra esa huella y los límites efectivos sin contactar al LLM. El pin opcional hace
fallar el arranque si no coincide. Para cambiar la variante, revisa y aplica el diff
exportado, instala el código y reinicia el servicio; los candidatos no se cargan
dinámicamente en un servidor concurrente. Mantén los archivos instalados inmutables
mientras corre el proceso. La huella identifica superficies, no sustituye la versión
del resto de la aplicación ni acredita calidad.

Cada investigación guarda `harness` y `harness_trace` en su estado. Las trazas registran
agentes, tools, parentesco y argumentos redactados, con secuencia y tiempo relativos
a cada nodo; `observed_at` conserva el instante UTC original de cada evento para
correlacionar reanudaciones. Dentro del mismo `clock_id` de proceso, `monotonic_ns`
permite ordenar ramas paralelas aunque el reloj UTC tenga poca resolución o cambie.
No se comparan ticks de distintos procesos; los checkpoints previos pueden carecer
de estos campos. Las evals consolidan una
secuencia de toda la ejecución sin reemplazar ese instante. Se reinician
al comenzar otro incidente en el mismo hilo. La API devuelve la identidad y límites;
historial/SSE conserva eventos `harness_trace` sin argumentos ni ventanas. Los eventos
parciales ya emitidos sobreviven a la cancelación en el ledger del servidor; el
checkpoint conserva las trazas de nodos terminados. El chat mantiene su política
anterior de no heredar un saver para la investigación anidada.

Se conservan checkpoints, streaming, cancelación, memoria, guardrails, presupuestos
fijos de llamadas por especialista y entregas explícitas e idempotentes. Los
checkpoints creados por este runtime sólo reanudan con el mismo perfil. Los anteriores,
sin identidad registrada, pueden reanudarse con el perfil actual: se registra a partir
del primer nodo serial completado, sin atribuirle retrospectivamente el trabajo previo.
Los contratos de diagnóstico por escenario y presupuestos medidos de tokens/latencia
permanecen en las evals; un incidente real no tiene una respuesta de referencia.

## Instalar y comprobar la optimización

```powershell
uv sync --locked --extra harness
uv run --extra harness fin-graph harness validate
uv run --extra harness better-harness validate harness/sre.toml
uv run --extra harness fin-graph harness smoke --output-dir .harness-runs/smoke
```

Smoke recorre 36 escenarios con clientes de infraestructura simulados y respuestas
scripted. Comprueba el cableado; **no mide calidad de un LLM**, ni optimiza. El reporte
marca `kind=wiring`, `quality_eligible=false`, `promotable=false`. Puede mostrar fallos
de calidad/contrato esperados: el script sólo consulta una fuente, sin resolver el RCA.
Cada ejecución necesita un directorio nuevo. Sin `--output-dir` se genera uno único.

## Qué puede optimizar

Las nueve superficies están declaradas en [`harness/sre.toml`](../harness/sre.toml).
Son archivos o atributos consumidos por el runtime real:

| Superficie | Tipo upstream | Efecto |
|---|---|---|
| `plan` | module_attr | Instrucciones del planificador |
| `specialist` | module_attr | Instrucciones comunes de especialistas |
| `synthesis` | module_attr | Síntesis del diagnóstico |
| `judge` | module_attr | Juez interno de claims |
| `tools` | workspace_file | Selección, orden y descripciones de tools disponibles |
| `skills` | workspace_file | Guía de investigación cargada en el prompt especialista |
| `planning` | workspace_file | Transformación del plan antes de las restricciones del grafo |
| `middleware` | workspace_file | Implementación de middleware adicional |
| `middleware_registration` | workspace_file | Registro de ese middleware por tarea |

El código editable vive en
[`graphs/investigation/strategy/`](../src/fin_graph/graphs/investigation/strategy/).
`prepare_tools(task, tools)` puede devolver una selección o copias con otras
`description`; debe conservar nombres, funciones, schemas y metadatos del adaptador.
`refine_plan(plan, alert, available)` puede modificar hipótesis/tareas; las restricciones
posteriores del grafo siguen limitando dominios, tareas y presupuesto.
`build_middleware(task)` añade middleware a la pila fija. Se reconstruye por delegación,
para que un especialista no reutilice la política de una tarea anterior.

Las reglas de datos no confiables, masking, guardrails, adaptadores externos, límites
del grafo, evaluadores, corpus y particiones quedan fuera del conjunto editable. No se
expone el código de escritura a infraestructura como una superficie de optimización.

## Qué verifican las evals

Se registran las invocaciones reales de servicios y los callbacks de tools, incluyendo
las fallidas: identificador, especialista padre, argumentos redactados, secuencia,
tiempo relativo y estado. Las ramas paralelas mantienen identidades separadas. Una
mención en texto o una tarea planeada no demuestra una invocación ejecutada.

Los contratos están en [`evals/trajectory.py`](../src/fin_graph/evals/trajectory.py).
Se construyen a partir de la referencia congelada y los metadatos de tools, sin recibir
instrucciones del optimizador. Para inspeccionar uno:

```powershell
uv run --extra harness fin-graph harness contract eks/002-image-pull-backoff
```

Un caso real aprueba sólo cuando **cada repetición** cumple:

- Ejecución completa, sin errores del grafo ni entregas externas.
- Categoría, keywords obligatorias/de descarte, fuentes, consultas y citas correctas.
- Invocaciones completas del planner, especialista exigido y sintetizador. RDS admite
  especialista de métricas o cloud, conforme a los dominios autorizados por las tools.
- Tools obligatorias ejecutadas con éxito y ninguna tool ajena al escenario.
- Tool dentro de una delegación activa y ejecutada por un dominio autorizado.
- Argumentos definidos por el contrato: namespace Kubernetes, identificador RDS,
  namespace/nombre de métrica y ventana temporal, según la tool. La ventana se verifica
  contra la delegación real, incluyendo ampliaciones legítimas del grafo.
- Precedencias explícitas, sin imponer un orden total a ramas que pueden correr en paralelo.
- Límites de llamadas, duplicados, errores, rondas del planner, latencia y tokens.
- Si se configura juez externo, score de referencia suficiente y evaluación completa.

`ArgumentRule` permite `equals`, `contains`, `one_of`, `gte`, `lte`, `exists` y rutas
anidadas; `ToolRequirement` admite mínimo/máximo de llamadas y propietario. El motor
soporta dependencias entre tools y entre agentes; los contratos actuales declaran las
dependencias de fases y las consultas exigidas por las referencias. No exige una
secuencia artificial idéntica para todas las investigaciones. Cada contrato lleva una
huella registrada en la procedencia del experimento.

Las comprobaciones de argumentos son las declaradas por escenario y las validaciones
del adaptador: no equivalen a demostrar que toda consulta semánticamente posible sea
óptima. El smoke y los tests con LLM scripted no demuestran generalización del modelo.

## Train, holdout y scorecard

[`dataset.json`](../src/fin_graph/harness/dataset.json) congela las familias:

| Split | Casos | Visibilidad y uso |
|---|---:|---|
| train | 19 | Referencias, contratos, resultados y trazas visibles al proposer |
| holdout | 10 | Se evalúa por candidato; detalles privados |
| scorecard | 7 | Sólo baseline y variante final |

Todos incluyen EKS y RDS. Se rechazan duplicados, familias entre splits, evidencia
idéntica cruzada, cobertura incompleta y cambios de corpus no versionados. El workspace
del proposer contiene únicamente los datos train y las superficies permitidas; el
feedback privado se limita a la decisión genérica de aceptación/rechazo.

El bit de aceptación también transmite información: limita las iteraciones. No ajustes
las familias tras observar resultados ni uses repetidamente scorecard para diseñar
prompts. Este corpus pequeño y correlacionado no acredita significancia estadística.

## Medición y optimización reales

Configura las claves de proveedor localmente; nunca en TOML o Git. Los identificadores
de modelos del ejemplo pueden cambiarse. La falta de credenciales genera estado
`blocked` antes de llamar al proposer.

```powershell
# Baseline, con tres repeticiones
uv run --env-file .env --extra harness fin-graph harness run --max-iterations 0 --repeat 3 --output-dir .harness-runs/baseline

# Optimización: modelos configurables, también pueden ser del mismo proveedor
uv run --env-file .env --extra harness fin-graph harness run --model openai:gpt-4.1-mini --proposer-model anthropic:claude-sonnet-4-6 --max-iterations 3 --repeat 3 --output-dir .harness-runs/optimization
```

El ciclo guarda procedencia, mide baseline, prepara el workspace oficial, recibe una
variante y evalúa train/holdout. Conserva el cambio sólo si mejora train y no pierde
ningún caso holdout anteriormente aprobado. Empates, regresiones y ejecuciones
incompletas se descartan. Una regresión en scorecard impide `promotable=true`.
Si train pasa entero, no hay cambios, o falla el proposer, el ciclo termina. No se
adopta automáticamente ninguna variante en producción.

Opciones `[sre]`:

| Opción | Default | Función |
|---|---:|---|
| repeat | 3 | Repeticiones por escenario; todas deben aprobar |
| case_timeout | 180 s | Deadline del grafo; el juez externo tiene otro deadline igual |
| proposer_timeout | 300 s | Deadline del proceso del Deep Agent externo |
| split_timeout | 3600 s | Deadline del proceso que evalúa un split |
| max_case_latency | 180 s | Presupuesto medido de latencia del agente SRE |
| max_case_tokens | 100000 | Presupuesto de tokens del agente SRE; uso desconocido no aprueba |
| judge_model | sin configurar | Juez externo contra referencia; independiente del juez de claims |
| min_judge_score | 0.8 | Umbral cuando se configura juez externo |

Para activar el juez de referencia, descomenta `judge_model` y `min_judge_score` en el
TOML. Sus credenciales se comprueban antes de iniciar. Los presupuestos de tokens del
agente SRE excluyen proposer y juez externo; no son un límite monetario global. No se
inventan precios. Con los defaults, tres candidatos y scorecard final pueden alcanzar
390 ejecuciones del agente, además del optimizador y del juez opcional.

Cada split se evalúa en una copia temporal de `src/fin_graph` con un intérprete nuevo.
Los cambios de archivos no se aplican al checkout activo. Un fallo de importación,
código inválido o timeout no puede contarse como aprobado. Los procesos de evaluación
se terminan al vencer su deadline y se conservan logs/artefactos.

**La copia de código y el subprocess no son un sandbox para Python hostil.** Los
candidatos comparten permisos de usuario y pueden acceder a la máquina. La separación
train/holdout es de investigación, no de seguridad. Para ejecutar código no confiable
usa una VM/contenedor/cuenta restringida y limita credenciales/red. Las trazas remotas
permanecen desactivadas en estas evals; las trazas locales son la fuente de verdad.

## Inspeccionar, exportar y adoptar

```powershell
uv run --extra harness fin-graph harness inspect .harness-runs/optimization
uv run --extra harness better-harness inspect .harness-runs/optimization
uv run --extra harness fin-graph harness export .harness-runs/optimization --output-dir .harness-runs/review
```

`export` crea los archivos de las superficies y `prompts.py`, `changes.diff` y un
`manifest.json` con hashes y elegibilidad. No modifica producción. También permite
exportar una baseline para revisión, indicando `promotable=false`. El destino debe ser
nuevo. La revisión y adopción de esos cambios es una acción separada.

| Artefacto | Contenido |
|---|---|
| report.md | Baseline/final, decisiones, fallos y recuentos por caso |
| summary.json | Calificación, métricas y procedencia SRE |
| report.json | Reporte oficial better-harness |
| variants/*.json | Contenido exacto de cada variante |
| source-baseline/ | Archivos originales capturados para un diff aplicable, con sus finales de línea |
| history/visible/train/ | Resultados, evidencia, diagnóstico, trazas y grader train |
| history/private/holdout/ | Lo mismo para holdout, fuera del workspace del proposer |
| history/private/scorecard/ | Baseline y resultado final reservado |
| history/visible/iterations/ | Propuestas, decisiones y workspace oficial |
| traces/*.json dentro de cada split | Invocaciones ordenadas, también en fallos parciales |
| worker.log dentro de cada split | Fallos de importación, ejecución o deadline |

`quality_eligible` acredita una evaluación real completa; `promotable` añade mejora
sin regresiones privadas. Ninguno garantiza que la calidad absoluta sea suficiente.
Revisa los casos restantes y prueba incidentes nuevos antes de desplegar. Un run
interrumpido en `running` no es exitoso. No se reanudan resultados obsoletos: inicia
una ejecución nueva.

## Compatibilidad y validación

```powershell
uv run --extra harness better-harness inventory harness/sre.toml
uv run --extra harness pytest tests/harness tests/evals -q
uv run --extra harness pytest evals/harness --harness-model scripted -q
uv run --extra harness pytest -q
```

La configuración conserva los dos tipos de superficie upstream. El runner pytest
oficial ejecuta los mismos graders y presupuesto configurado, normalizando JUnit en
Windows. Puedes usar `--harness-config` con otra configuración. El CLI nativo
`better-harness run` mantiene la aceptación original por suma train+holdout y aplica
sus propias sustituciones temporales de archivos: para copias aisladas, deadlines y
protección emparejada usa `fin-graph harness run`.

Harbor es un runner alternativo de upstream para benchmarks distintos; no aporta un
backend a estas evals RCA basadas en pytest. No se instaló como dependencia adicional.
El CI del harness corre en Windows y Linux: tests, smoke, exportación y 36 casos pytest,
sin secretos. Los tests normales conservan la cobertura del producto; Postgres exige
su servicio. Las pruebas con proveedores reales requieren claves y no se confunden
con verificaciones offline.
