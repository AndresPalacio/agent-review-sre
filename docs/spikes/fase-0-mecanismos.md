# Spike de la Fase 0: mecanismos de LangGraph verificados

Versiones: langgraph 1.2.14, langchain 1.4.3, langchain-core 1.6.7, langgraph-checkpoint 4.2.0,
langgraph-checkpoint-sqlite 3.1.1, langgraph-checkpoint-postgres 3.1.2, langgraph-cli 0.4.33.
Entorno: Python 3.12 sobre Windows 11. La fila 8 y el test de contrato de la fila 5 se agregaron en la Fase 1a.

| # | Mecanismo | Resultado | Consecuencia para el diseño | Test |
|---|---|---|---|---|
| 1 | `Send` hacia un nodo que corre un subagente `create_agent` | Cada rama corre su subagente; los hallazgos se fusionan con un reducer `operator.add` | El nodo `specialist` de la Fase 2 sigue este patrón; en tests, una instancia de modelo por rama | `tests/spikes/test_send_fanout.py` |
| 2 | `interrupt()` dentro de una tool de un subagente | Burbujea al supervisor: `ainvoke` devuelve `__interrupt__` y `aget_state().next` no está vacío; `Command(resume=...)` reanuda las dos capas | Las aprobaciones de chat y remediación usan este camino; solo el grafo externo lleva checkpointer | `tests/agents/test_subagents.py` |
| 3 | `@task` dentro de un nodo de `StateGraph` | Al reanudar, el nodo se re-ejecuta desde el inicio, pero el `@task` ya completado se reproduce desde el checkpoint sin repetir el efecto; el código normal antes de `interrupt()` sí se repite | El nodo `execute` de remediación envuelve cada acción en `@task`, y nada con efectos va antes de `interrupt()` | `tests/spikes/test_task_in_node.py` |
| 4 | Runtime context al reanudar | No se persiste: un resume sin `context=` corre con `runtime.context = None` | El RunManager de la Fase 4 reconstruye y pasa el context en cada invoke y en cada resume | `tests/spikes/test_context_on_resume.py` |
| 5 | Middleware propio con `wrap_tool_call` | Funciona; un middleware solo síncrono falla con `NotImplementedError` bajo `ainvoke` | Todo middleware propio implementa `wrap_tool_call` y `awrap_tool_call` | `tests/agents/middleware/test_tool_cache.py`, `tests/spikes/test_middleware_async_contract.py` |
| 6 | Salida estructurada con `ToolStrategy` | `structured_response` trae la instancia Pydantic; la tool se llama como la clase | Los especialistas devuelven su hallazgo con `ToolStrategy`; el supervisor lo recibe como JSON | `tests/agents/test_factory.py`, `tests/agents/test_subagents.py` |
| 7 | Context hacia subagentes | `subagent_tool` reenvía `runtime.context` y las tools del subagente lo ven | Clientes y credenciales viajan en el context hasta los especialistas | `tests/agents/test_subagents.py` |
| 8 | `interrupt()` dentro de un subgrafo compilado usado como nodo | El padre, que es el único con checkpointer, se pausa con el payload del subgrafo; `Command(resume=...)` reanuda dentro del subgrafo, y un rechazo no ejecuta nada | El subgrafo de remediación de la Fase 5 se monta como nodo del grafo de investigación, sin checkpointer propio | `tests/spikes/test_subgraph_node_interrupt.py` |

## Hallazgos de entorno

- **psycopg async en Windows** requiere un loop selector. Los tests lo fijan con el hook `pytest_asyncio_loop_factories`; el override de `event_loop_policy` está deprecado en pytest-asyncio 1.4. El servidor en Windows lo necesitará en la Fase 4.
- **`langgraph dev`** corre en Windows sin licencia ni LangSmith (`GET /ok` → `{"ok":true}`). Si se lanza desde un script, al detenerlo hay que matar también el proceso hijo del reloader; en una terminal basta con Ctrl+C.
- **`init_chat_model`** construye el modelo sin API key y falla recién al invocar, así que los grafos de Studio importan sin claves.
- **pydantic-settings no exporta `.env` a `os.environ`.** Las claves de proveedor (`ANTHROPIC_API_KEY`, …) llegan por entorno: `langgraph dev` las carga vía `"env": ".env"`, y el CLI y el servidor llamarán `load_dotenv()` en su raíz de composición (Fase 2).
- **python-dotenv ya ignora el BOM** de un `.env` guardado con el Bloc de notas.
