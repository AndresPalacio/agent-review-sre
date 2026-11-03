# Agentes de `fin-graph`

Este paquete contiene únicamente componentes que deciden con un modelo. Las transiciones
deterministas, reintentos, checkpoints y límites viven en `workflows` y `runtime`.

Hay cuatro rutas fáciles de localizar:

| Ruta | Entrada principal | Patrón |
|---|---|---|
| `analysis` | `persona_team.py` | Supervisor DeepAgents con 19 personas financieras |
| `chat` | `supervisor.py` | Supervisor conversacional con herramientas y órdenes paper |
| `solver` | `supervisor.py` | Supervisor DeepAgents acotado con dos hijos asíncronos |
| `optimizer` | `proposer.py` | Agente opcional que edita un workspace aislado |

DeepAgents construye supervisores y subagentes capaces de delegar o usar herramientas. LangGraph
orquesta los workflows deterministas que los invocan, conserva estado y aplica rutas de control.
Un nodo de LangGraph no se cataloga como agente si no contiene una decisión de modelo.

Para explorar sin crear modelos ni hacer llamadas externas:

```powershell
uv run fin-graph agents list
uv run fin-graph agents describe analysis.persona_team
```

El catálogo ejecutable está en `catalog.py`; valida IDs únicos, relaciones y fábricas.
