# Demo de agentes sin credenciales

Desde la raíz del repositorio, ejecuta:

```powershell
uv run python examples/demo_agentes.py
```

Necesitas Python 3.12 y `uv`, como el proyecto. La primera ejecución puede descargar
las dependencias del proyecto; después la demo trabaja con datos locales.
No requiere `.env`, Docker, Kubernetes, Datadog ni una clave de un proveedor LLM.

## Qué vas a ver

El incidente predefinido es un contenedor de `payments-api` que registra
`OOMKilled`. El ejemplo ejecuta el grafo de investigación real:

```text
Alerta → contexto → plan → especialista → herramienta local → síntesis
                                                               ↓
                                primera verificación: cita inexistente
                                                               ↓
                              refinar → plan → especialista → síntesis
                                                               ↓
                                segunda verificación: evidencia citada
                                                               ↓
                                               propuesta → reporte
```

- **Workflow determinista:** el grafo controla el orden y la repetición.
- **Plan-and-Execute:** se plantea una hipótesis y una tarea para Kubernetes.
- **ReAct:** un modelo guionado solicita `k8s_list_pods`, recibe el resultado
  de una herramienta real respaldada por un cliente simulado y termina su turno.
- **Critic:** la primera síntesis cita deliberadamente un ID inexistente.
  El verificador real rechaza la cita. La segunda usa el ID recopilado y una regla
  didáctica comprueba que los datos contienen `OOMKilled`.

El plan y la síntesis están predefinidos; el juez es una regla, no un LLM.
La segunda ronda repite la consulta para enseñar el ciclo, no descubre nuevos datos.
Se verifica una observación, no la causa subyacente del exceso de memoria.
Esta demo muestra un especialista, no el supervisor de chat ni un swarm.
No ejecuta remediaciones ni envía reportes a servicios externos.

## Run / Debug para entender al orquestador

Para recorrerlo sin IDE, con una pausa después de cada etapa:

```powershell
uv run python examples/demo_agentes.py --step
```

Pulsa **Enter** para continuar o escribe **q** para salir. Se muestran el plan,
las tareas restantes, las llamadas a herramientas, sus evidencias y la verificación.
El grafo se interrumpe después de guardar el checkpoint y reanuda con `None`,
conservando la misma investigación. Mientras lees, no queda un nodo ejecutándose.
Los checkpoints están en memoria: cerrar el proceso termina esta sesión didáctica.

Para ejecutar todo mostrando los mismos checkpoints, sin esperar entrada:

```powershell
uv run python examples/demo_agentes.py --inspect
```

### PyCharm: configuración compartida

El repositorio incluye
[SRE Graph - Orquestador offline](../.run/SRE%20Graph%20-%20Orquestador%20offline.run.xml).
Usa el intérprete del módulo `sre-graph`, configurado en el proyecto como `uv (sre-graph)`.
Si falta el entorno, ejecuta `uv sync` y selecciona `.venv/Scripts/python.exe`.

1. Abre [demo_agentes.py](demo_agentes.py) y busca `# BREAKPOINT` en
   `inspect_checkpoint`. Pon el breakpoint en la siguiente línea ejecutable,
   `view: dict[str, Any] = {`.
2. Selecciona **SRE Graph - Orquestador offline** y pulsa **Debug**.
3. Inspecciona las variables `node`, `state` y `next_nodes`. Usa **Resume** para
   llegar al siguiente checkpoint. Con **Run** se imprime todo sin detenerse.
4. Si quieres detenerte únicamente en la decisión de repetir, usa la condición
   `node == "verify"` en ese breakpoint.

Observaciones que puedes añadir al panel **Watches**:

| Expresión | Qué explica |
|---|---|
| `state.get("available_domains")` | Qué especialidades están disponibles. |
| `state.get("plan")` | Hipótesis y tareas elegidas por el planificador guionado. |
| `state.get("wave")`, `state.get("budget")` | Ronda actual y número de tareas restantes. |
| `state.get("evidence")` | Respuestas originales de las herramientas locales. |
| `state.get("harness_trace")` | Llamadas, argumentos redactados, parentesco y resultados. |
| `state.get("diagnosis")`, `state.get("verification")` | Conclusión propuesta y si las citas la respaldan. |
| `next_nodes` | Próximo paso que realmente programó LangGraph. |

En la primera parada de `verify` verás `passed=False` y `next_nodes=("refine",)`.
En la segunda verás `passed=True` y `next_nodes=("propose_remediation",)`.
La demo fija dos rondas y dos tareas totales. Este presupuesto cuenta tareas;
no implementa las reservas de coste compartidas que propone el simulador HTML.

### Entrar en el código de cada decisión

Para seguir una ejecución dentro de sus nodos, coloca breakpoints adicionales:

| Archivo y función | Variables o acción que observar |
|---|---|
| [demo_agentes.py](demo_agentes.py), `DemoServices.plan` | `available`, `findings`, `self.round` y el plan que se propone. |
| [graph.py](../src/sre_graph/graphs/investigation/graph.py), `plan` | `candidate`, `chosen`, `max_tasks`: cómo se restringe el plan. |
| [graph.py](../src/sre_graph/graphs/investigation/graph.py), `dispatch` | `tasks` y los `Send` que despachan al especialista. |
| [runner.py](../src/sre_graph/evals/runner.py), `ScriptedServices.investigate` | `name`, `args`, `result`: petición de tool, ejecución y observación. |
| [fixtures.py](../src/sre_graph/evals/fixtures.py), `FixtureKubernetes.list_pods` | La herramienta obtiene datos locales en lugar de consultar un clúster. |
| [demo_agentes.py](demo_agentes.py), `DemoServices.synthesize` | La primera cita es inexistente; la segunda usa evidencia recopilada. |
| [graph.py](../src/sre_graph/graphs/investigation/graph.py), `after_verify` | Verificación, rondas, presupuesto y tiempo determinan si se repite. |

Las pausas en `inspect_checkpoint` quedan fuera de los timeouts de los nodos.
Al entrar dentro de un nodo sí aplica el timeout de la demo, fijado en 30 minutos.

El modelo real usaría `LangChainServices.plan` en
[services.py](../src/sre_graph/graphs/investigation/services.py). Esta demo sustituye
esa llamada por un plan guionado para que puedas repetir y depurar el recorrido
sin credenciales. Las transiciones, filtros del plan, tools, verificador de citas,
checkpoints y trazas del harness sí usan el código del proyecto.

### Prueba automatizada que también puedes depurar

```powershell
uv run pytest tests/examples/test_demo_agentes.py::test_debug_checkpoints_expose_committed_state_and_resume_without_restarting -q
```

En PyCharm también puedes abrir [test_demo_agentes.py](../tests/examples/test_demo_agentes.py)
y usar **Debug** sobre esa prueba. Pon un breakpoint en su función
`inspect_checkpoint` para observar los mismos estados. Comprueba que solo hay una
entrada de alerta, dos rondas, dos llamadas reales a la tool local y verificación
`False → True` al reanudar.

Esto cubre el orquestador de investigación implementado. La fachada MCP `work_*`,
WorkService, los agentes hijos durables y el registro completo de releases del
[diseño propuesto](../docs/arquitecture-review/2026-10-22-revision-rovo.md) todavía no
están implementados. Esta demo usa una integración nativa local; no inicia un servidor MCP.

## Probar por endpoint

```powershell
uv run python examples/demo_agentes.py --serve
```

Abre <http://127.0.0.1:8010/docs>. En cada operación usa **Try it out → Execute**:

1. `POST /v1/investigations`: sin body ni credenciales. Ejecuta el caso completo
   y devuelve `thread_id`.
2. `GET /v1/investigations/{thread_id}`: pega ese ID para consultar evidencia,
   diagnóstico y verificación.
3. `GET /v1/investigations/{thread_id}/explain`: muestra las etapas recorridas;
   busca `verify` con `passed: false`, luego `refine` y finalmente `passed: true`.

También puedes usar otra terminal PowerShell:

```powershell
$demo = Invoke-RestMethod -Method Post http://127.0.0.1:8010/v1/investigations
Invoke-RestMethod "http://127.0.0.1:8010/v1/investigations/$($demo.thread_id)/explain" |
    ConvertTo-Json -Depth 10
```

Esta API educativa es independiente de la API de producción: usa los mismos
 nombres de tres rutas, pero espera el resultado del POST (HTTP 200), simplifica
las respuestas y conserva solo las últimas 20 ejecuciones en memoria. Al cerrar
con `Ctrl+C`, se pierden. Solo escucha en `127.0.0.1:8010`.
