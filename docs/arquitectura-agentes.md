# Arquitectura de agentes de `fin-graph`

`fin-graph` combina dos recorridos. El **chat** es un supervisor conversacional que decide cuándo consultar especialistas o lanzar una investigación. La **investigación RCA** (análisis de causa raíz) es un grafo con etapas fijas: planifica hipótesis, ejecuta especialistas en paralelo, reúne evidencia, redacta un diagnóstico y comprueba sus afirmaciones. Por eso no corresponde asignarle una sola etiqueta de agente a todo el sistema: el chat se parece a **ReAct con supervisor**, mientras que el RCA es principalmente un **workflow determinista con agentes**, planificación y crítica.

El punto de entrada determina qué ocurre. Una pregunta libre llega a `POST /v1/chat/threads/{thread_id}/messages`; el supervisor puede responder usando especialistas o llamar a la herramienta `investigate`. Una investigación explícita llega a `POST /v1/investigations` con `text` o `alert` y devuelve `run_id` y `thread_id`. Ambas rutas se construyen en [la API](../src/fin_graph/server/app.py); el supervisor y su herramienta de investigación están en [el grafo de chat](../src/fin_graph/graphs/chat/graph.py).

## Los siete patrones y su presencia en el sistema

| Patrón | Cómo funciona | Presencia en `fin-graph` |
| --- | --- | --- |
| **1. ReAct (Reason + Act)** | Un modelo decide una llamada a herramienta, recibe el resultado y decide el siguiente paso hasta producir una respuesta. | **Sí, dentro del chat y de los especialistas.** El supervisor y cada especialista se crean con `create_agent`, que permite el ciclo de llamadas a herramientas. En RCA, los especialistas solo reciben herramientas de lectura de su dominio y tienen límites de llamadas. El grafo RCA completo no es un único bucle ReAct. Véanse [fábrica de agentes](../src/fin_graph/agents/factory.py), [servicios RCA](../src/fin_graph/graphs/investigation/services.py) y [catálogo de especialistas](../src/fin_graph/graphs/investigation/specialists.py). |
| **2. Supervisor / Router** | Un coordinador elige qué especialista recibe una tarea y reúne su respuesta. | **Sí en chat; parcialmente en RCA.** El chat expone los especialistas como herramientas del supervisor. En RCA, el planificador elige tareas y dominios disponibles, pero el código del grafo controla el despacho con `Send`. No hay un supervisor LLM que controle todas las transiciones del RCA. Véanse [subagentes](../src/fin_graph/agents/subagents.py) y [grafo RCA](../src/fin_graph/graphs/investigation/graph.py). |
| **3. Plan-and-Execute** | Un plan separa el objetivo en hipótesis y tareas; la ejecución aporta hallazgos para continuar o replantear. | **Sí en RCA.** El planificador propone hipótesis comprobables y tareas por dominio; `constrain_plan` limita las tareas a especialistas disponibles. Tras la verificación, el grafo puede refinar la ventana y volver a planificar. Los límites por defecto son tres olas, cinco tareas por ola, quince tareas en total y 180 segundos. Véanse [modelos de plan](../src/fin_graph/domain/investigation.py) y [límites y transiciones](../src/fin_graph/graphs/investigation/graph.py). |
| **4. Swarm / Handoff** | Especialistas se transfieren el control entre sí según lo descubierto, sin coordinador central obligatorio. | **No como patrón de control.** RCA ejecuta especialistas en paralelo mediante `Send`; cada uno devuelve hallazgos al estado compartido y el grafo continúa en síntesis. Un especialista no entrega directamente el control a otro. La delegación del chat también pasa por el supervisor. Véanse [despacho RCA](../src/fin_graph/graphs/investigation/graph.py) y [subagentes del chat](../src/fin_graph/agents/subagents.py). |
| **5. Hierarchical Multi-Agent** | Hay varios niveles de coordinación: coordinador, subcoordinadores y ejecutores. | **Solo una jerarquía simple en chat.** Hay supervisor y especialistas, pero no subcoordinadores ni varios niveles de mando. En RCA, el planificador, los especialistas, el sintetizador y el juez cumplen funciones distintas dentro de un grafo fijo. Véanse [grafo de chat](../src/fin_graph/graphs/chat/graph.py) y [servicios RCA](../src/fin_graph/graphs/investigation/services.py). |
| **6. Reflection / Critic** | Una segunda pasada revisa si la respuesta se sostiene y pide corrección cuando detecta huecos. | **Sí en RCA.** `verify_diagnosis` comprueba estructura y citas; luego un modelo `judge` evalúa cada afirmación con la evidencia citada. Si la verificación no pasa y queda presupuesto, el grafo vuelve a planificar. Si termina sin pasar, la causa raíz queda marcada como no verificada y la confianza se limita. Véanse [verificación](../src/fin_graph/domain/verification.py), [juez](../src/fin_graph/graphs/investigation/services.py) y [bucle RCA](../src/fin_graph/graphs/investigation/graph.py). |
| **7. Workflow determinista + agentes** | El código decide las etapas y sus transiciones; los modelos resuelven los pasos que necesitan interpretación. | **Es la arquitectura principal del RCA.** `intake`, `gather_context`, `plan`, `specialist`, `synthesize`, `verify`, `refine`, `propose_remediation`, `report` y `deliver` están conectados explícitamente en LangGraph. Extracción, planificación, investigación, síntesis y juicio usan modelos; el despacho, los límites, la verificación básica y el cierre siguen reglas de código. Véanse [grafo RCA](../src/fin_graph/graphs/investigation/graph.py) y [servicios de razonamiento](../src/fin_graph/graphs/investigation/services.py). |

Los siete especialistas estables cubren **Kubernetes, métricas, logs, trazas, nube, cambios y errores**. Solo aparecen como disponibles si el registro contiene herramientas de lectura para su dominio. La lista no implica que todas las integraciones estén configuradas en cada instalación; [la selección](../src/fin_graph/graphs/investigation/specialists.py) depende de las herramientas realmente presentes.

## Recorrido de una pregunta

Por ejemplo, ante «¿Por qué aumentó la latencia de checkout después del despliegue?», el chat recibe el mensaje dentro de un hilo persistente. El supervisor puede consultar métricas y cambios como subagentes y responder con lo observado. Si necesita un RCA completo, llama a `investigate`, que inicia el grafo de investigación con la pregunta como alerta solicitada por el usuario. La API también permite iniciar directamente ese RCA con `POST /v1/investigations`.

```mermaid
flowchart TD
    Q[Pregunta del usuario] --> CHAT[POST /v1/chat/threads/{id}/messages]
    Q --> API[POST /v1/investigations]
    CHAT --> SUP[Supervisor de chat: ciclo modelo / herramienta]
    SUP --> ASK[Especialistas de lectura por dominio]
    ASK --> SUP
    SUP -->|investigate cuando necesita RCA| INTAKE
    API --> INTAKE[Intake: normalizar alerta y ventana]
    INTAKE --> CONTEXT[Contexto: herramientas disponibles, memoria y runbooks]
    CONTEXT --> PLAN[Plan: hipótesis y tareas acotadas]
    PLAN -->|Send por tarea| WORKERS[Especialistas en paralelo: consultar herramientas y devolver evidencia]
    WORKERS --> SYN[Síntesis: diagnóstico con IDs de evidencia]
    SYN --> VERIFY[Verificación: reglas y juez por afirmación]
    VERIFY -->|No pasa y queda presupuesto| REFINE[Refinar ventana / plan]
    REFINE --> PLAN
    VERIFY -->|Pasa o se agota el presupuesto| REPORT[Proponer acciones y construir reporte]
    REPORT --> DELIVER[Entrega opcional]
    DELIVER --> SUP
    DELIVER --> RESULT[GET /v1/investigations/{thread_id}]
    SUP --> ANSWER[Respuesta del chat]
```

En el RCA, `intake` interpreta el texto o alerta y delimita la ventana temporal. `gather_context` identifica herramientas de lectura realmente configuradas, busca incidentes previos y selecciona runbooks. Esos antecedentes orientan hipótesis; **no sustituyen evidencia del incidente actual**. El planificador formula hipótesis y asigna tareas a dominios disponibles. `Send` permite ejecutar esas tareas en paralelo, y cada especialista registra hallazgos y evidencia con identificadores que el diagnóstico puede citar.

Después, el sintetizador redacta el diagnóstico. La verificación primero revisa si las citas existen y si las afirmaciones cumplen las reglas; un juez evalúa si los datos citados respaldan cada afirmación. Si falta respaldo, el grafo puede ampliar la ventana de observación y volver a planificar dentro de sus límites. Si aun así no verifica la causa, el reporte comunica la incertidumbre. El RCA **propone** remediaciones; una acción que modifique sistemas sigue el flujo separado de preparación, aprobación y verificación descrito en [el grafo de chat](../src/fin_graph/graphs/chat/graph.py) y [el grafo de remediación](../src/fin_graph/graphs/remediation/graph.py).

Esta es la parte observable de la «magia»: el modelo decide qué consultar dentro de las herramientas permitidas, las llamadas producen datos reales, el sistema conserva sus referencias y un paso de verificación contrasta el diagnóstico con esas referencias. El resultado depende de las integraciones y modelos configurados; cuando faltan herramientas o el razonamiento falla, el grafo usa rutas de degradación y puede terminar con causa desconocida en vez de inventar una conclusión.

## Qué muestra el endpoint de explicación

`GET /v1/investigations/{thread_id}/explain` se usa **después** de crear una investigación. Su propósito es presentar juntas la **topología** del grafo (las etapas y transiciones posibles) y la **ejecución observada** de ese hilo (las etapas recorridas, especialistas que participaron, olas, hallazgos y resultado de la verificación según lo registrado). Así se distingue entre «el sistema podría volver a planificar» y «esta ejecución volvió a planificar».

El endpoint es de lectura: consultar la explicación no inicia otra investigación. El identificador `thread_id` procede de `POST /v1/investigations`; el estado y el reporte siguen disponibles en `GET /v1/investigations/{thread_id}`, y la progresión temporal en `GET /v1/threads/{thread_id}/history` o su stream. La explicación conecta esas piezas en una vista del funcionamiento del agente. Expone decisiones observables, referencias de evidencia y estados, **no el razonamiento privado del modelo**, mensajes internos completos ni cargas crudas de herramientas.

La respuesta separa `topology.nodes` y `topology.edges` (rutas posibles) de `execution.steps` (eventos guardados en orden). `execution.waves` agrupa planes y especialistas por ronda; `findings`, `evidence`, `verification` y `diagnosis` resumen el checkpoint actual. Cada elemento lleva `provenance`: `observed` si procede de un evento persistido y `derived` si se reconstruye del estado. `run` describe la corrida más reciente, mientras que cada paso conserva su propio `run_id` cuando un hilo se reanuda. La explicación entrega como máximo 500 pasos y marca `steps_truncated` si hay más; `next_after_id` permite continuar con el historial paginado. En la consola, abre una investigación y pulsa **Cómo investigó** para ver esta estructura.
