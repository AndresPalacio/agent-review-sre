# bm-workflow

Biblioteca Python para definir workflows tipados y versionados, compilarlos a LangGraph
y ejecutarlos con contratos de entrada/salida. Es un paquete independiente: no importa
`fin_graph`, ni necesita la aplicación financiera, un servidor, credenciales o proveedores.

El núcleo utiliza **Pydantic 2 y LangGraph 1**, con Python 3.12 o posterior; LangSmith
se declara para controlar las trazas durante la simulación. Los modelos, herramientas y
clientes externos los aporta la aplicación consumidora. DeepAgents es opcional.

## Instalación y ejemplo offline

Desde la raíz del repositorio:

```console
uv sync --project packages/workflow --locked
uv run --project packages/workflow --locked python packages/workflow/examples/quickstart.py
```

Para instalarlo en otra aplicación desde este checkout:

```console
pip install ./packages/workflow
```

O construir el wheel e instalar el archivo generado:

```console
uv build --project packages/workflow --wheel --out-dir dist/workflow
pip install dist/workflow/bm_workflow-0.2.0-py3-none-any.whl
```

Para utilizar el constructor opcional de DeepAgents:

```console
pip install "./packages/workflow[agents]"
```

El nombre de distribución es `bm-workflow`; el import es `bm_workflow`.

## Primer workflow

Los handlers tipados reciben y devuelven modelos Pydantic. `artifact_version` identifica
la revisión de su implementación declarada por el consumidor.

```python
from pydantic import BaseModel, ConfigDict
from bm_workflow import (
    RunStatus,
    WorkflowRegistry,
    compile_workflow,
    typed_step,
    workflow,
)


class Number(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int


def increment(number: Number) -> Number:
    return Number(value=number.value + 1)


definition = workflow(name="numbers", version="1", input=Number, output=Number).flow(
    typed_step(
        "increment",
        increment,
        input_schema=Number,
        output_schema=Number,
        artifact_version="1",
    )
)
compiled = compile_workflow(definition)
registry = WorkflowRegistry([compiled])
result = registry.run(name="numbers", version="1", input_data={"value": 2})
assert result.status == RunStatus.COMPLETE
assert isinstance(result.output, Number)
assert result.output.value == 3
```

En una aplicación async, usar `await registry.arun(name=..., version=..., input_data=...)`.
También existen `run_workflow(compiled, input_data)` y
`await arun_workflow(compiled, input_data)` para ejecutar sin registro. La fachada sync
se utiliza fuera de un event loop activo.

El [ejemplo ejecutable](examples/quickstart.py) añade un segundo paso, admisión/ejecución
persistidas y simulación con fixtures. Todos sus datos y handlers son locales.

## Contratos y ejecución

`workflow(...).flow(...)` conserva contratos y bindings en definiciones inmutables.
`compile_workflow` valida la definición y construye el grafo ejecutable de LangGraph;
LangGraph ejecuta su topología. La biblioteca no mantiene un segundo motor de grafos.

| API | Contrato |
| --- | --- |
| `step(id, handler, ...)` | Recibe un diccionario y devuelve actualizaciones de estado. |
| `typed_step(id, handler, *, input_schema, output_schema, artifact_version)` | Valida entrada y salida del handler; admite handlers sync y async. |
| `compile_workflow(definition)` | Entrega definición, grafo y `definition_digest`. |
| `WorkflowRegistry([...])` | Resuelve versiones explícitas; rechaza identidades duplicadas. |
| `registry.run(name=..., version=..., input_data=...)` | Ejecuta y entrega un `RunResult`. |
| `registry.arun(...)` | Variante async de la ejecución. |
| `run_workflow` / `arun_workflow` | Ejecutan un compilado directamente, con contexto opcional. |

Los datos de negocio viajan en `state["data"]`. Cada handler recibe una copia aislada;
sus actualizaciones se fusionan superficialmente con los datos acumulados. La entrada
y salida globales se validan en ejecución, también cuando llegan como instancias Pydantic.
Los contratos aceptan alias y nombres de campo; el estado usa nombres de campo canónicos.
La serialización conserva representaciones de ida y vuelta de Pydantic (por ejemplo `Json[T]`).
Un contrato de nodo no demuestra por sí solo todas las propiedades del estado acumulado.

`RunResult.status` distingue `COMPLETE`, `FAILED`, `TIMED_OUT` y `CANCELLED`.
En éxito, `output` es el modelo de salida validado; ante fallo, `cause` contiene una causa
segura. Los mensajes originales de proveedores no forman parte de ese contrato de error.
Los errores anteriores al inicio, como una entrada inválida o una versión inexistente,
pueden lanzar excepciones.

La identidad de una definición incorpora contratos, topología, revisiones declaradas y
políticas. No es un hash de todo el código Python: al cambiar un handler, actualizar su
`artifact_version` y la revisión desplegada. El consumidor conserva el manifest de releases
y decide cuándo publicar otra versión.

`registry.manifest()` devuelve las identidades registradas.
`registry.release_manifest(previous=historial)` rechaza publicar el mismo nombre/versión
con otro digest y devuelve el historial combinado; no lo escribe ni despliega nada.

Las políticas son explícitas. Por ejemplo, para una lectura revisada que pueda fallar
con `TransientError` y tolere repetición:

```python
from bm_workflow import ExecutionPolicy, step

read_node = step(
    "lookup",
    trusted_lookup,
    kind="external",
    artifact_version="1",
    policy=ExecutionPolicy(
        max_attempts=3,
        safe_to_retry=True,
        retry_delay=0.1,
        timeout_seconds=5,
    ),
)
```

Aquí `trusted_lookup` es un handler del host que recibe/devuelve diccionarios. Tres
intentos significan el inicial y hasta dos retries. Las excepciones arbitrarias y los
timeouts no activan retries. Marcar como `external` un paso que hace I/O permite a Dry Run
bloquearlo cuando falta un fixture; la biblioteca no puede deducir esa clasificación
inspeccionando cualquier función Python.

Para un deadline global, construir `RunContext(name, version, definition_digest,
deadline=time.monotonic() + segundos)` y pasarlo como `context=` a la ejecución.
`context.cancellation.set()` señala cancelación cooperativa; `current_context()` permite
consultar el contexto dentro del handler sin mezclarlo con los datos de negocio.

## Composición y adaptadores

Las primitivas públicas de `bm_workflow` producen pasos componibles:

| Primitiva | Comportamiento |
| --- | --- |
| `sequence(*steps)` | Agrupa una secuencia para reutilizarla con `*sequence(...)`. |
| `route(id, by, routes, default=...)` | Selecciona una rama por etiqueta; el router ocupa el último lugar del flujo. |
| `parallel(id, branches, *, output_key, max_concurrency=4)` | Ejecuta ramas aisladas con concurrencia acotada y reúne resultados por nombre. |
| `join(id, handler, ...)` | Paso explícito que reduce los resultados materializados del paralelo. |
| `map_each(id, body, *, items_key, output_key, max_concurrency=4, max_items=1000)` | Procesa una colección acotada; conserva el orden de entrada. |
| `subflow(id, compiled, *, output_key)` | Ejecuta un workflow hijo y coloca su salida validada bajo una clave. |

El body de `map_each` recibe el estado padre junto con `item` e `item_index`; sus resultados
son estados completos, ordenados. Los límites de concurrencia pertenecen a cada composición;
no constituyen una cuota distribuida entre procesos. Las ramas pueden haber producido
efectos antes de que otra falle; no hay rollback automático.

`bm_workflow.adapters` acepta dependencias ya construidas por el host:

- `tool_step`: herramienta inyectada, schemas explícitos y mapeo de entrada opcional.
- `llm_step`: modelo inyectado con salida estructurada y prompt explícito.
- `agent_step`: grafo de agente inyectado y salida validada.
- `deep_agent_step`: factory inyectable; la factory predeterminada importa DeepAgents
  de forma perezosa y requiere el extra `agents`.

Los adaptadores guardan el resultado validado bajo `output_key` y se marcan como fronteras
externas para simulación. No construyen credenciales, no seleccionan endpoints desde el
payload y no conceden autorización al usuario. Esas decisiones pertenecen al host.

## Agentes con soluciones verificadas

La integración opcional `bm_workflow.agents` añade `build_solver_agent` y `solver_step`.
DeepAgents conserva su bucle de planificación, herramientas y delegación. La aplicación
define el problema y aporta un verificador que observa el resultado real: pruebas,
consultas al sistema o comprobaciones sobre el artefacto producido.

El verificador es la autoridad para declarar una solución `verified`. Una respuesta
estructurada válida o la opinión de otro modelo no sustituyen las comprobaciones.
Cada criterio de aceptación debe tener una comprobación aprobada. El resultado distingue
`verified`, `failed`, `blocked` e `inconclusive`; `RunStatus.COMPLETE` solo indica que
terminó la ejecución del workflow.

Si un turno contiene herramientas y una propuesta estructurada, primero se ejecutan
las herramientas pendientes mediante el motor nativo y después se verifica el entorno.
El verificador recibe una copia de los datos de la propuesta; los clientes y el contexto
del host conservan su identidad. Se vuelven a validar el problema y los límites incluso
si provienen de `model_construct` o `model_copy`, que pueden omitir validadores de Pydantic.

El host inyecta `host_model`, `host_tools` y un verificador; este ejemplo de composición
usa una comprobación de salud real proporcionada por la aplicación:

```python
from pydantic import BaseModel
from bm_workflow.agents import (
    VerificationCheck,
    VerificationReport,
    build_solver_agent,
    solver_step,
)


class Solution(BaseModel):
    summary: str


async def verify(problem, candidate, context):
    healthy = await context.host_context.healthcheck()
    return VerificationReport(
        verdict="verified" if healthy else "failed",
        checks=[VerificationCheck(criterion_id="healthy", passed=healthy)],
    )


agent = build_solver_agent(
    model=host_model,
    tools=host_tools,
    solution_schema=Solution,
    verifier=verify,
)
node = solver_step("solve", agent, solution_schema=Solution, output_key="resolution")
```

La entrada del paso es un `ProblemSpec` serializado con `id`, `goal` y
`acceptance_criteria={"healthy": "La comprobación de salud pasa"}`. `problem_mapper`
permite extraerlo de un estado mayor. Para aportar el servicio de salud, usar
`invocation_factory=lambda state, run: AgentInvocation(context=health_service)`.
El verificador recibe `SolverContext`, cuyo `host_context` conserva ese objeto y cuyo
`workflow_context` conserva el deadline y la cancelación del workflow.

Un fallo reparable devuelve al modelo las comprobaciones fallidas mediante middleware
nativo y permite hasta dos correcciones. Cuando faltan datos o herramientas, se devuelve
un bloqueo explícito. Un error del verificador nunca se convierte en aprobación.

El verificador también puede recibir `candidate=None` si el agente termina sin una
propuesta, por ejemplo al reconocer una capacidad faltante. Puede devolver `blocked`
con los requisitos ausentes; una aprobación sin propuesta se convierte en `inconclusive`.
Las propuestas aprobadas se comprueban otra vez al terminar para detectar cambios
posteriores en el entorno.

Construir el grafo una vez durante el ciclo de vida del host y reutilizarlo dentro de
su event loop. Las ejecuciones necesitan contextos nuevos; los modelos, herramientas y
clientes conservan su identidad. `agent_step` y `deep_agent_step` aceptan
`invocation_factory(state, run_context)` para entregar `AgentInvocation(config, context)`.
El adaptador `deep_agent_step` mantiene su construcción perezosa por invocación.

`AgentManifest(revisions={...})` añade revisiones declaradas al digest del workflow:
modelo, prompt, herramientas, skills y políticas. Solo admite referencias de versión
de tipo string. No colocar credenciales ni objetos de clientes en ese manifest.

### Límites y capacidades

Los límites predeterminados compartidos por una ejecución son 24 llamadas de agente,
48 llamadas a herramientas, cuatro llamadas de resumen, dos especialistas simultáneos,
120 segundos y dos reparaciones. Las llamadas de resumen tienen un contador separado;
una llamada al SDK puede contener reintentos internos del proveedor. Los límites no son
una cuota monetaria ni una cuota distribuida entre procesos.

Los resúmenes usan el prompt, las referencias a archivos/medios y la recuperación ante
exceso de contexto de DeepAgents/LangChain. Cada intento nativo de resumen consume una
reserva de `max_summary_calls`, incluido el intento con historial reducido. La cuota,
el deadline y la cancelación también cubren esa recuperación; no se amplía el presupuesto.

`StateBackend` conserva archivos virtuales temporales. Para aplicar una solución se
necesitan herramientas de acción del host; guardar un plan en un archivo no equivale a
ejecutarlo. Las skills aportan instrucciones bajo demanda. Las herramientas y el backend
determinan qué operaciones pueden realizarse; no se habilita un shell implícitamente.
El especialista `general-purpose` creado automáticamente hereda las skills del padre;
los especialistas personalizados reciben únicamente sus fuentes declaradas. La carga de
skills de los hijos comparte deadline y cancelación. El nombre de herramienta `task`
está reservado para la delegación nativa y no puede usarse para una herramienta del host.

El host debe usar `EffectCoordinator` y el protocolo del destino para escrituras que
necesiten idempotencia. La corrección de una solución no autoriza a repetir una operación
cuyo resultado sea incierto. Los timeouts son cooperativos: no pueden detener un thread
sync ni revertir efectos externos que ya empezaron.

Una excepción arbitraria de una herramienta termina la ejecución: puede representar
una escritura cuyo resultado se desconoce. Para fallos conocidos y recuperables, el host
puede usar el mecanismo nativo `ToolException` junto con `tool.handle_tool_error=True`
o un mensaje seguro definido por el host. Esto entrega el error al modelo para que elija
otra acción; no activa un reintento automático. El host debe clasificar el fallo antes
de habilitar este comportamiento y conciliar los efectos inciertos por separado.

### Ejemplo y evaluación

El [ejemplo de reparación](examples/solver_offline.py) ejecuta herramientas locales y
comprueba el estado modificado. El [ejemplo multiagente](examples/solver_team.py) delega
en un solucionador y un especialista verificador mediante la herramienta nativa `task`.
El verificador recibe herramientas de observación declaradas por el host con
`metadata={"read_only": True}`; esa declaración debe corresponder al comportamiento real
de la herramienta. El callback del host conserva la aprobación definitiva.
El [benchmark pareado](examples/solver_benchmark.py)
compara un agente básico de LangChain con el solver, usando entornos nuevos por caso y
los mismos modelos, herramientas y límites. Incluye configuración, transformación de
datos, artefactos e investigación de incidentes, además de errores de herramientas,
evidencia falsa y requisitos faltantes. La investigación necesita descubrir tres fuentes
mediante un manifiesto, cruzar inventario, observaciones y política, y producir un artefacto
comprobable. Su variante reservada cambia rutas, identificadores, umbral y respuesta esperada.

```console
uv run --project packages/workflow --locked --extra agents python packages/workflow/examples/solver_offline.py
uv run --project packages/workflow --locked --extra agents python packages/workflow/examples/solver_team.py
uv run --project packages/workflow --locked --extra agents python packages/workflow/examples/solver_benchmark.py
uv run --project packages/workflow --locked --extra agents python packages/workflow/examples/solver_benchmark.py --case investigate-holdout
```

Por defecto se usan modelos guionizados sin red: estos resultados prueban contratos,
no superioridad de razonamiento. Para evaluar un modelo real, pasar explícitamente
`--model-factory modulo:funcion`; la función debe crear una instancia nueva del modelo
para cada brazo del experimento. El proveedor y sus credenciales los configura el host.
Se rechaza una factory que reutilice el mismo objeto de modelo entre casos o brazos.

El JSON incluye el resumen agregado y `by_split` para separar contratos y variantes
reservadas. Estas variantes son públicas en el repositorio; no constituyen una evaluación
ciega de generalización. Las métricas tienen los siguientes significados:

| Métrica | Interpretación |
| --- | --- |
| `environment_resolution_rate` | El evaluador independiente comprueba el estado final, aunque el agente no declare éxito. |
| `verified_resolution_rate` | Estado final aprobado **y** declaración de éxito, con el mismo criterio en ambos brazos. |
| `host_verified_resolution_rate` | Aprobación interna del solver confirmada por el evaluador; el baseline no tiene esa aprobación interna. |
| `recoveries` / `incorrect_effects` | Resoluciones después de fallos observados de escritura o escrituras incorrectas; no se infiere recuperación por la etiqueta del escenario. |
| `total_model_calls` | Todas las llamadas observadas, incluidos resúmenes; `model_calls` y `summary_calls` permiten desglosarlas. |
| `total_tokens` / `observed_tokens` | Total solo si todas las llamadas aportan uso; el subtotal observado se conserva sin presentarlo como coste completo. |
| `perturbation_scope` | Distingue perturbaciones reales del entorno de diagnósticos/evidencia falsa inyectados únicamente en el modelo guionizado. |

Se reportan además falsos éxitos, bloqueos, efectos duplicados, presupuesto, duración y
consumo por caso resuelto. Los fallos de ejecución permanecen visibles en cada fila.
La duración incluye construcción del grafo y ejecución; una observación por caso no
permite concluir ventajas estadísticas de latencia.

Ambos brazos reciben las mismas herramientas del host y cuotas de llamadas de agente y
herramientas. El solver conserva planificación, archivos, delegación y verificación nativos,
además de su cuota separada de resúmenes; el baseline no resume. Comparar
`total_model_calls` permite incluir ese trabajo adicional. La evaluación compara estos
dos sistemas completos, no atribuye una diferencia a un único componente.
Estas cuotas iguales de agente/herramientas no constituyen un presupuesto total de
inferencia idéntico cuando hay resúmenes. Una comparación futura de eficiencia con
proveedores debe igualar también llamadas totales, tokens o coste; las pruebas locales
no acreditan esa equivalencia ni una ventaja de razonamiento.

La revisión de compatibilidad y las mejoras están descritas en el
[plan de fiabilidad](../../docs/superpowers/plans/2026-11-03-solver-reliability.md).
La integración conserva los mecanismos oficiales de
[resumen de DeepAgents 0.7.32](https://github.com/langchain-ai/deepagents/blob/deepagents==0.7.32/libs/deepagents/deepagents/middleware/summarization.py),
[contexto y aislamiento](https://docs.langchain.com/oss/python/deepagents/context-engineering)
y [herramientas y middleware de LangChain](https://docs.langchain.com/oss/python/langchain/middleware/built-in).

## Runs persistidos y efectos

La ejecución directa conserva el resultado en memoria. `RunService` añade admisión,
snapshot de identidad, namespace y transiciones mediante compare-and-swap (CAS):

```python
from bm_workflow.persistence import InMemoryRunStore, RunService

service = RunService(
    registry,
    InMemoryRunStore(),
    artifact_revision="deployment-1",
    namespace="example",
)
accepted = service.admit(name="numbers", version="1", input_data={"value": 2})
terminal = service.execute(accepted.run_id, expected_version=accepted.version)
print(terminal.state, terminal.output_data)
```

`RunRecord.version` es la revisión CAS del registro; `workflow_version` es la versión
funcional. `aexecute` permite ejecutar desde código async. `get` consulta un run del
namespace y `cancel(..., expected_version=...)` cancela un run todavía aceptado.
`SQLiteRunStore(path)` sustituye el store en memoria y conserva registros entre procesos;
soporta `close()` y un bloque `with`. El registro almacena datos reales de entrada y salida.

```text
ACCEPTED → RUNNING → SUCCEEDED / FAILED / TIMED_OUT / CANCELLED
        ↘ CANCELLED
```

Un CAS ganador reclama la ejecución. La revisión de artefacto y el digest deben coincidir
con lo admitido. Un estado terminal no se reemplaza por un resultado tardío. Si un proceso
cae después de reclamar `RUNNING`, hace falta conciliación administrativa: guardar el
registro no equivale a guardar checkpoints de cada nodo ni autoriza a repetir el grafo.

Para escrituras, `bm_workflow.effects` ofrece `EffectCoordinator` con
`InMemoryEffectStore` o `SQLiteEffectStore`. Su `execute(key, handler, payload)` devuelve
un resultado confirmado anterior sin invocar de nuevo el handler; reutilizar una clave
con otro payload produce conflicto. Una intención pendiente o un resultado incierto
bloquean el replay automático.

El ledger y el sistema remoto no comparten una transacción. La biblioteca **no promete
exactly-once**: el destino debe aplicar su protocolo real de idempotencia. El host debe
verificar externamente los resultados inciertos antes de decidir cómo conciliarlos.

## Simulación y validación

`dry_run` utiliza el mismo grafo compilado e intercepta los nodos mediante fixtures:

```python
from bm_workflow import dry_run

report = dry_run(compiled, {"value": 2}, fixtures={"increment": {"value": 3}})
print(report.result.status, report.events)
```

Un fixture representa las actualizaciones completas que sustituirán al nodo. Las fronteras
externas sin fixture se bloquean. Los handlers deterministas revisados por el consumidor
pueden ejecutarse; la simulación dentro del proceso no confina código Python arbitrario.
Sus eventos muestran la ejecución observada, no cobertura exhaustiva de todas las rutas.
LangSmith es dependencia directa para desactivar su tracing durante la simulación;
usar esta biblioteca no requiere una cuenta ni credenciales de LangSmith.

Para comprobar el paquete sin instalar el producto principal:

```console
uv sync --project packages/workflow --locked --extra agents
uv run --project packages/workflow --locked --extra agents ruff check packages/workflow
uv run --project packages/workflow --locked --extra agents ruff format --check packages/workflow
cd packages/workflow
uv run --locked --extra agents mypy
uv run --locked --extra agents pytest
```

El [workflow CI independiente](../../.github/workflows/workflow-library.yml) ejecuta estos
checks, los ejemplos y el build en Linux/Windows con Python 3.12/3.14. Ejecuta primero
los contratos del núcleo sin el extra y después la integración real de DeepAgents.
Además instala el wheel en un entorno
limpio en Linux, ejecuta un flujo y comprueba que `fin_graph` y DeepAgents no estén instalados.

## Arquitectura y límites

```text
Host: modelos, tools, clientes, identidad y configuración
                         ↓ inyección
definition / spec → compiler (LangGraph) → registry por nombre y versión
                         ↓
                   runtime + patterns
                         ↓
          resultado validado / persistencia CAS opcional
                         ↓
            efectos y adaptadores proporcionados por el host
```

El paquete es independiente de los grafos existentes de `fin_graph`; esos grafos conservan
sus propias fábricas, harness y servidores. Los componentes nuevos no importan el producto.

- Los deadlines y la cancelación son cooperativos. Un timeout no puede detener un handler
  sync que ya corre en un thread ni revertir un efecto externo.
  La fachada sync espera el cierre de sus threads antes de devolver; un handler sync
  bloqueado puede por ello retrasar el retorno incluso después del deadline.
- Un timeout no justifica reintentar una escritura. Los retries requieren operación segura
  y error transitorio clasificado; no hay retries ciegos por cualquier excepción.
- No hay recuperación durable por nodo, cola distribuida, autenticación HTTP ni cuotas
  globales de concurrencia. SQLite persiste registros, no sustituye un scheduler distribuido.
- Namespace separa claves de almacenamiento; no autentica a un actor ni sustituye ownership
  y autorización en una API expuesta.
- Output válido y éxito técnico no demuestran calidad funcional. La aplicación debe evaluar
  su dominio con datos y criterios propios.

## Cobertura frente a la referencia 1–24

La referencia es `como-funciona-workflow.md` (baseline del 2 de noviembre de 2026), que
describe otra aplicación con 94 módulos e integraciones corporativas. Este paquete adapta
sus contratos reutilizables; no reproduce sus fuentes, hashes, lock ni servicios privados.
La tabla distingue lo implementado de lo que requiere composición o trabajo adicional.

| Sección | Cobertura y diferencias |
| --- | --- |
| 1. Organización | Paquete independiente `bm_workflow`; host externo, sin Flask/Fury. |
| 2. Conceptos | Definiciones, nodos, compilados, versiones, contexto, runs y efectos. |
| 3. Definición/compilación | Builder y LangGraph; validación de topología y contratos runtime. No se afirma equivalencia con el demostrador completo de compatibilidad JSON Schema de la referencia. |
| 4. Estado/contratos | Canal `data`, copias aisladas, actualizaciones superficiales y modelos Pydantic. |
| 5. Versiones/identidad | Resolución explícita y digest de definición; historial de releases conservado por el host. |
| 6. Ejecución directa | Fachadas sync/async y resultados seguros con output validado. |
| 7. Runs/ciclo de vida | Memoria/SQLite, admisión separada y CAS. Sin KVS corporativo, drenaje distribuido ni recuperación por nodo. |
| 8. Composición | Secuencia, rutas terminales, paralelo, join, map ordenado y subflows. Sin promesa de los modos BEST_EFFORT o pools idénticos a la referencia. |
| 9. Modelos/prompts/agentes | Adaptadores opcionales, contexto por ejecución y solver DeepAgents con comprobación del host, reparaciones y límites compartidos. Sin catálogo corporativo ni tarifas. |
| 10. Tools/MCP | Tool inyectada y validación; MCP se conecta desde el host. Sin discovery MCP, transporte ni protección de destinos propios. |
| 11. Efectos/idempotencia | Ledger con replay confirmado, conflicto e incertidumbre; sin garantía remota automática. |
| 12. Autorización/aislamiento | Namespace de almacenamiento. Identidad, permisos y auditoría de acceso pertenecen al host. |
| 13. Colas | Admisión/ejecución separadas reutilizables; sin BigQueue, WorkQueue ni adaptadores de entrega. |
| 14. Dry Run | Intercepción del mismo grafo y fixtures; sin paridad con shadow, LIVE auditado o sandbox. |
| 15. Calidad | Contratos offline y benchmark pareado de resolución con grader independiente; modelo real por opt-in. Sin framework genérico de promoción; el harness del producto es independiente. |
| 16. Configuración/observabilidad | Configuración Python explícita y eventos de ejecución; sin bootstrap OTel ni melitk-config. |
| 17. Ejemplo local | Quickstart, reparación, delegación multiagente y benchmark ejecutables sin red ni credenciales. |
| 18. Límites | Documentados arriba; éxito local no acredita recursos productivos. |
| 19. Entorno/dependencias | `pyproject.toml` y lock propios; Pydantic/LangGraph y control de tracing LangSmith, DeepAgents opcional. No conserva el lock corporativo. |
| 20. Algoritmos del núcleo | Builder, compilación, runtime y composiciones propios; no réplica binaria ni equivalencia de cada algoritmo privado. |
| 21. Modelos/tools/MCP | Puertos inyectables; sin GenAI corporativo, tarifas, scope registry ni transporte MCP propios. |
| 22. Persistencia/seguridad/transporte | CAS y ledger locales; sin KVS/Authz/Fury ni gateway HTTP autorizado. |
| 23. Simulación/evaluación/telemetría | Simulación y checks de paquete; evaluación de negocio, promoción y exportación OTel a cargo del consumidor. |
| 24. Reproducción/composición | Instalación aislada, ejemplo, CI y wheel; sin imágenes Fury, índices privados ni manifest de las fuentes originales. |

Las decisiones y el alcance están en el
[diseño](../../docs/superpowers/specs/2026-11-02-workflow-library-design.md) y el
[plan](../../docs/superpowers/plans/2026-11-02-workflow-library.md).
La ampliación de agentes está descrita en el
[plan de soluciones verificadas](../../docs/superpowers/plans/2026-11-02-verified-solver.md).
