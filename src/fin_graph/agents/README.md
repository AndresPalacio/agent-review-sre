# Agentes de `fin-graph`

Este paquete contiene únicamente componentes que deciden con un modelo. Las transiciones
deterministas, reintentos, checkpoints y límites viven en `workflows` y `runtime`.

Hay cuatro rutas fáciles de localizar:

| Ruta | Entrada principal | Patrón |
|---|---|---|
| `analysis` | `persona_team.py` | Fan-out LangGraph determinista sobre 19 personas Deep Agents |
| `chat` | `supervisor.py` | Supervisor conversacional con herramientas y órdenes paper |
| `solver` | `supervisor.py` | Supervisor DeepAgents acotado con dos hijos asíncronos |
| `optimizer` | `proposer.py` | Agente opcional que edita un workspace aislado |

Todo loop autónomo dirigido por un modelo en `src/fin_graph` termina en
`deepagents.create_deep_agent`. La seam común `common/factory.py:make_agent` sirve para agentes
pequeños: por defecto expone solo las herramientas entregadas por el llamador y reemplaza los
slots nativos de filesystem, shell y delegación síncrona por middleware sin herramientas.
`allow_synchronous_subagents=True` es un opt-in explícito, no una capacidad implícita.

Hay dos contratos de delegación nativa:

- `common/subagents.py:make_supervisor` convierte grafos hijos en `CompiledSubAgent(mode="isolated")`
  y los alcanza con `task`. Las llamadas pueden emitirse en paralelo, pero el supervisor espera sus
  resultados.
- `solver/supervisor.py` declara `AsyncSubAgent` para `fundamentals` y `technicals`. Agent Server
  aporta `start_async_task`, `update_async_task`, `cancel_async_task`, `check_async_task` y
  `list_async_tasks`, con contexto, task IDs y notificaciones al padre. Para ejecutar un supervisor
  y `N` hijos asíncronos a la vez se dimensionan al menos `1 + N` workers.

LangGraph orquesta los workflows deterministas que invocan esos agentes y conserva routing,
orden, aprobaciones, fan-out, checkpoints, CAS y ledgers de efectos. El grafo exterior de
`analysis.persona_team` es uno de esos control planes: su fan-out está fijado en código y sus 19
hijos narradores sí son Deep Agents. Un nodo de LangGraph no se convierte en agente por contener
una transición o ejecutar un hijo.

Para explorar sin crear modelos ni hacer llamadas externas:

```powershell
uv run fin-graph agents list
uv run fin-graph agents describe analysis.persona_team
```

El catálogo ejecutable está en `catalog.py`; valida IDs únicos, relaciones y fábricas. Sus clases
model-driven son `deepagents_supervisor` y `deepagents_subagent`: ya no existe
`langchain_agent`, y `analysis.native_react` es un subagente Deep Agents construido por
`common/factory.py:make_agent`.
