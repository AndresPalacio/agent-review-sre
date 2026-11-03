# Matriz de configuración

Las variables modernas tienen prioridad. Si una variable moderna y su alias histórico están
presentes con valores distintos, el arranque falla indicando el conflicto.

| Propósito | Variable moderna | Alias aceptado |
|---|---|---|
| Proveedor LLM | `LLM_PROVIDER` | `DEFAULT_MODEL_PROVIDER` |
| Modelo principal | `LLM_MODEL_REASONING` | `DEFAULT_MODEL_NAME`, `V2_LLM_MODEL` |
| Modelos worker/fast/judge | `LLM_MODEL_*` | `DEFAULT_MODEL_NAME` |
| Datos de mercado | `MARKET_DATA_PROVIDER` | `DATA_PROVIDER` |
| App local | `FIN_GRAPH_LOCAL` | `LOCAL` |
| API compatible | `FIN_GRAPH_LEGACY_API` | `LEGACY_API` |
| Permitir bind remoto compatible | `FIN_GRAPH_ALLOW_INSECURE_LEGACY_API` | `ALLOW_INSECURE_LEGACY_API` |
| Proxies confiables | `FIN_GRAPH_TRUSTED_PROXIES` | `TRUSTED_PROXIES` |

## Perfiles

| Perfil | Persistencia | Bind | Autenticación | Uso |
|---|---|---|---|---|
| `fin-graph app` | SQLite | `127.0.0.1` | Compatible local + clave nativa generada | Escritorio/desarrollo |
| Compose `app` | SQLite en volumen | `0.0.0.0:8000` | Perfil compatible permitido explícitamente | Host local controlado |
| `fin-graph serve` | Postgres | configurable | API key por rol obligatoria | Servicio remoto |
| Compose `distributed` | Postgres/pgvector | `0.0.0.0:8001` | Bootstrap admin + claves por rol | Multi-worker |

## Familias de variables

- `LLM_*`, `OLLAMA_*` y la clave oficial del proveedor controlan inferencia.
- `YAHOO_*`, `FINANCIAL_DATASETS_*`, `MARKET_*` controlan datos point-in-time.
- `TRADING_*` y `PAPER_*` fijan límites que el modelo no puede cambiar.
- `PERSISTENCE_BACKEND`, `SQLITE_PATH`, `DATABASE_URL` seleccionan almacenamiento.
- `FIN_GRAPH_*` define el perfil compatible, directorios y artefactos.
- `BOOTSTRAP_ADMIN_KEY`, `MAX_*`, `THREAD_*` controlan el servidor distribuido.
- `SLACK_DELIVERY_*`, `TELEGRAM_DELIVERY_*` habilitan entrega saliente.
- `SLACK_GATEWAY_*`, `TELEGRAM_GATEWAY_*` habilitan entradas y aprobadores.
- `WEBHOOK_TOKENS`, `WEBHOOK_HMAC_SECRETS` autentican señales financieras.
- `FIN_ANALYSIS_*` fija presupuestos y la huella del harness.

`.env.example` enumera el conjunto soportado y puede cargarse directamente. Los valores vacíos se
tratan como ausentes. Nunca pongas claves en variables `VITE_*` ni en una URL.
