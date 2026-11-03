# Manifiesto de API

Todos los cuerpos son JSON salvo los streams SSE y las respuestas estáticas de la UI. Las rutas
compatibles conservan el perfil de la referencia; las nativas requieren `X-API-Key` excepto health.

## Compatibilidad AI Hedge Fund

| Método | Ruta | Contrato |
|---|---|---|
| GET | `/health`, `/ping` | Liveness y stream de ping |
| GET | `/hedge-fund/agents` | Catálogo de 19 analistas |
| POST | `/hedge-fund/run` | Ejecución SSE del fondo |
| POST | `/hedge-fund/backtest` | Backtest compatible SSE |
| GET | `/language-models/` | Modelos cloud/Ollama |
| GET | `/language-models/providers` | Proveedores conocidos |
| CRUD | `/api-keys/` | Credenciales de proveedor |
| CRUD | `/flows/` | Flujos visuales |
| CRUD | `/flows/{flow_id}/runs/` | Runs, ciclos, latest/active y cancelación |
| GET/POST/DELETE | `/ollama/*` | Servicio y modelos locales |
| GET | `/v2/skills` | Skills financieras y recursos |
| GET | `/v2/{model}/predict` | Señales cuantitativas versionadas |
| POST | `/v2/{model}/train` | Artefactos de entrenamiento local |
| GET | `/v2/risk/var` | Value at Risk |
| GET | `/v2/algorithms` | Catálogo de estrategias |
| POST | `/v2/backtests` | Backtest algorítmico asíncrono |
| GET | `/v2/backtests/{run_id}` | Estado y resultado |
| GET | `/v2/backtests/{run_id}/events` | Eventos SSE |
| POST | `/v2/backtests/{run_id}/cancel` | Cancelación cooperativa |

## API nativa

| Método | Ruta | Rol mínimo |
|---|---|---|
| GET | `/v1/health`, `/v1/ready` | público |
| GET | `/v1/session` | viewer |
| POST | `/v1/api-keys` | admin |
| POST/GET | `/v1/analyses`, `/v1/analyses/{thread_id}` | operator/viewer |
| POST/GET | `/v1/chat/threads/{thread_id}/*` | operator/viewer |
| GET | `/v1/threads/{thread_id}/stream` | viewer |
| GET | `/v1/portfolio` | viewer |
| POST/GET | `/v1/orders` | operator/viewer |
| POST | `/v1/orders/{thread_id}/approve` | approver |
| CRUD | `/v1/schedules` | admin/viewer |
| GET/POST | `/v1/integrations` | viewer/operator |
| POST | `/v1/signals/{source}` | token o HMAC de fuente |
| POST | `/v1/gateway/slack`, `/v1/gateway/telegram` | firma del proveedor |

FastAPI publica OpenAPI en `/openapi.json` y documentación interactiva en `/docs` cuando el perfil
lo permite. La lista ejecutable de contratos está en `tests/compat/fixtures/manifest.json`.
