# Fase 1c — Observabilidad: plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** consultar, mediante herramientas de solo lectura y verificables sin red, Prometheus, Alertmanager, Grafana, Loki, Tempo, Mimir, Datadog y New Relic.

**Architecture:** Cada endpoint tiene su propia integración e ID en `IntegrationRegistry`, por lo que un servicio ausente no oculta herramientas de otro. Un cliente HTTP async compartido limita tiempo/tamaño, codifica parámetros y convierte fallos en `IntegrationError`; cada paquete conserva sus settings, métodos de lectura, tools con `ToolMeta` y `verify`. Las credenciales solo viven en `RunContext`, nunca en estado ni argumentos de tools.

**Tech Stack:** Python 3.12, pydantic-settings, httpx 0.28, LangChain tools, pytest con `httpx.MockTransport`, uv; versiones fijadas en `uv.lock`.

**Spec:** `docs/specs/2026-10-07-fin-graph-design.md`, sección Integraciones, ola 1.

## Global Constraints

- Solo lectura en 1c. Silences, annotations, mute y otras acciones mutantes quedan para el flujo HITL de la fase 5.
- Registro explícito y activación `<PREFIJO>_ENABLED=true`; cliente nuevo por corrida y tools seleccionadas por dominio desde `RunContext`.
- Respuesta de tool `(content, artifact)` con JSON compacto, salida del modelo redactada por el middleware existente y artifact limitado a 2 MiB por respuesta HTTP.
- Solicitudes con timeout, sin redirecciones, sin credenciales en mensajes de error y sin interpolar URL arbitraria desde argumentos de tools.
- Prosa y configuración en español; identificadores, código y docstrings en inglés. Sin dependencia ni import de OpenSRE.
- Gate: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run lint-imports`, `uv run pytest` (Postgres local cuando esté disponible).

## Review Focus

1. Respuesta HTTP 200 con error de Prometheus o GraphQL: `test_prometheus_rejects_error_envelope`, `test_graphql_errors_are_tool_errors`.
2. Credenciales en error, redirect o respuesta enorme: `test_http_errors_do_not_echo_credentials`, `test_http_redirect_is_not_followed`, `test_oversized_response_is_rejected`.
3. Ventana inválida o consulta excesiva: `test_query_range_rejects_invalid_window`, `test_datadog_logs_have_bounded_window`, `test_nrql_rejects_non_select_queries`.
4. Proveedor parcialmente configurado: `test_only_enabled_observability_endpoints_register_tools`, `test_verify_all_isolates_a_failed_endpoint`.
5. Página incompleta, cabecera de tenant o error de permisos: `test_datadog_logs_preserve_next_cursor`, `test_mimir_passes_tenant_header`, `test_verify_requires_read_access`.

## Decisiones y ajustes al diseño

- La tabla del diseño agrupa productos; este plan asigna ocho IDs (`prometheus`, `alertmanager`, `grafana`, `loki`, `tempo`, `mimir`, `datadog`, `newrelic`) porque URLs, permisos y disponibilidad difieren. Coste: ocho entradas de configuración en vez de cuatro, a cambio de fallo aislado y verificación precisa.
- Mimir usa la API de consulta compatible con Prometheus; Loki y Tempo usan sus APIs nativas. Grafana se usa para alert rules, annotations y dashboards. No se hace proxy por Grafana, que exigiría UIDs de datasource y permisos adicionales.
- `verify` ejecuta una lectura autenticada y barata por servicio, nunca solo un health anónimo. Una cuenta sin permisos de lectura queda `failed`.
- New Relic `aiIssues` requiere la cabecera experimental `nerd-graph-unsafe-experimental-opt-in: AiIssues`; el riesgo de cambio de esquema se documenta. Errores APM se consultan por NRQL sobre `TransactionError`.
- Los listados hacen una página acotada y conservan `nextCursor`/`after` cuando existe; no recorren páginas automáticamente. El especialista puede refinar la búsqueda.
- Se añade `httpx` como dependencia directa; los tests usan `MockTransport`, sin `respx` nuevo.
- La tool de errores APM recibe segundos Unix y los convierte a milisegundos para `SINCE`/`UNTIL` de NRQL. La búsqueda de entidades expone el cursor de continuación de NerdGraph y acepta el cursor de la página siguiente.
- El registro resume errores de validación de settings sin sus valores de entrada; el transporte convierte también errores de codificación de cabeceras en fallos seguros.

## Mapa de archivos e interfaces

| Archivo | Responsabilidad |
|---|---|
| `integrations/http.py` | `HttpJsonClient.request(method, path, params=None, json_body=None, headers=None) -> Any`, `prometheus_data`, `require_window`, límite 2 MiB y errores seguros |
| `integrations/prometheus/{settings,client,tools,integration}.py` | PromQL, range, labels/series, alerts/rules/targets y variantes Mimir |
| `integrations/alertmanager/{settings,client,tools,integration}.py` | Alertas y silences existentes |
| `integrations/grafana/{settings,client,tools,integration}.py` | Alert rules, annotations y dashboards |
| `integrations/loki/{settings,client,tools,integration}.py` | LogQL range y labels |
| `integrations/tempo/{settings,client,tools,integration}.py` | TraceQL search y trace ID |
| `integrations/datadog/{settings,client,tools,integration}.py` | Logs, metrics, monitors, events, spans e incidents |
| `integrations/newrelic/{settings,client,tools,integration}.py` | NRQL, issues, incidents, entity search y errores APM |
| `integrations/catalog.py`, `.env.example`, `README.md` | Registro y configuración explícita; instrucciones de uso y verificación |
| `tests/integrations/{http,prometheus,alertmanager,grafana,loki,tempo,datadog,newrelic}/` | Casos offline de contrato, permisos, límites y selección de tools |

Fuentes de endpoints: [Prometheus HTTP API](https://prometheus.io/docs/prometheus/latest/querying/api/), [Mimir HTTP API](https://grafana.com/docs/mimir/latest/references/http-api/), [Loki HTTP API](https://grafana.com/docs/loki/latest/reference/loki-http-api/), [Tempo HTTP API](https://grafana.com/docs/tempo/latest/api_docs/), [Grafana HTTP API](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/), [Datadog API](https://docs.datadoghq.com/api/latest/), [NerdGraph](https://docs.newrelic.com/docs/apis/nerdgraph/get-started/introduction-new-relic-nerdgraph/).

---

### Task 1: Transporte HTTP seguro y contrato común

**Files:** `pyproject.toml`, `uv.lock`, `src/fin_graph/integrations/http.py`, `tests/integrations/test_http.py`.

**Interfaces:** Produce `HttpJsonClient(base_url: str, headers: Mapping[str,str] | None = None, timeout_seconds: float = 15, transport: httpx.AsyncBaseTransport | None = None)`; `request(...) -> Any`; `prometheus_data(payload: Any, vendor: str) -> Any`; `require_window(start: int, end: int, max_seconds: int = 604800) -> None`.

- [ ] Escribir pruebas offline de JSON, parámetros, timeout, HTTP 401/429/5xx, redirect, cuerpo inválido/voluminoso, URL sin host y ventana inversa o >7 días. El error solo puede incluir proveedor, método, ruta y código, nunca URL completa, body ni headers.
- [ ] Ejecutar `uv run pytest -q tests/integrations/test_http.py` y observar fallos por ausencia del módulo.
- [ ] Añadir dependencia directa `httpx>=0.28,<0.29`; implementar el cliente con `httpx.AsyncClient`, `follow_redirects=False`, `trust_env=False` y cierre por llamada. Convertir `httpx.RequestError` y `ValueError` en `IntegrationError`; comprobar `Content-Length`/bytes antes de JSON. Permitir únicamente rutas relativas internas.
- [ ] Repetir pruebas y gate completo; registrar resultado y commit `feat(integrations): add bounded async HTTP transport`.

### Task 2: Prometheus, Mimir y Alertmanager

**Files:** los tres paquetes correspondientes, `integrations/catalog.py`, `.env.example`, tests de los tres paquetes.

**Interfaces:** `PrometheusClient.query`, `query_range`, `labels`, `series`, `alerts`, `rules`, `targets`; `MimirIntegration` reutiliza el cliente con tenant y publica las cuatro consultas de métricas; `AlertmanagerClient.alerts`, `silences`. Cada clase implementa `Integration[Settings, Client]`.

- [ ] Escribir tests de settings disabled/enabled, URL y token, rutas/params y envelope de error, ventana y step, cabecera `X-Scope-OrgID`, metadata de tools y `verify` con 403. Usar `MockTransport`, sin red.
- [ ] Ejecutar tests nuevos y observar fallo esperado.
- [ ] Implementar los métodos y tools de lectura; `tool_payload` entrega resumen y artifact; cada `verify` hace una lectura autenticada (`query=up` o alerts), con `VerifyResult.OK` solo si responde.
- [ ] Repetir tests y gate; registrar resultado y commit `feat(integrations): add Prometheus Mimir and Alertmanager readers`.

### Task 3: Grafana, Loki y Tempo

**Files:** los tres paquetes, `integrations/catalog.py`, `.env.example`, tests de los tres paquetes.

**Interfaces:** `GrafanaClient.alert_rules`, `annotations`, `dashboards`; `LokiClient.query_range`, `labels`; `TempoClient.search`, `trace`. Cada tool se enruta a `Domain.METRICS`, `LOGS`, `TRACES`, `CHANGES` o `ERRORS` según la evidencia.

- [ ] Escribir tests offline para auth, rutas/params, tiempo Loki en nanosegundos, Tempo trace ID validado, límites, metadata, respuesta de error y verify con credenciales válidas/inválidas.
- [ ] Ejecutar tests y observar fallo.
- [ ] Implementar settings/client/tools/integration por endpoint; `verify` usa `/api/search?limit=1`, `/loki/api/v1/labels` y búsqueda Tempo limitada a cinco minutos.
- [ ] Repetir tests y gate; registrar resultado y commit `feat(integrations): add Grafana Loki and Tempo readers`.

### Task 4: Datadog

**Files:** `integrations/datadog/`, `integrations/catalog.py`, `.env.example`, tests Datadog.

**Interfaces:** `DatadogClient.logs`, `metrics`, `monitors`, `events`, `spans`, `incidents`; usa `DATADOG_API_KEY`, `DATADOG_APP_KEY` y `DATADOG_SITE`; mantiene una página y su cursor.

- [ ] Escribir tests offline de headers, body de logs/spans, timestamps, límites/paginación, las seis tools y dominios, 401/403/429, y verify autenticado a monitors.
- [ ] Ejecutar tests y observar fallo.
- [ ] Implementar settings/client/tools/integration sin SDK adicional; las seis llamadas usan rutas documentadas y una página acotada.
- [ ] Repetir tests y gate; registrar resultado y commit `feat(integrations): add Datadog readers`.

### Task 5: New Relic, registro final y documentación

**Files:** `integrations/newrelic/`, `integrations/catalog.py`, `.env.example`, `README.md`, `docs/progress/2026-10-07-fase-1c.md`, tests New Relic y catálogo.

**Interfaces:** `NewRelicClient.nrql`, `issues`, `incidents`, `entities`, `apm_errors`; GraphQL con variables, status/error envelope verificado y cabecera experimental solo en `aiIssues`.

- [ ] Escribir tests offline para clave y región, variables GraphQL, errores HTTP/GraphQL, NRQL no SELECT, issues/incidents con cabecera opt-in, entidades, errores APM y verify con lectura del account. Probar el catálogo completo con integraciones apagadas, una sola activa y un endpoint roto.
- [ ] Ejecutar tests y observar fallo.
- [ ] Implementar paquete y registro explícito de los ocho IDs. Documentar variables, tools, comprobación offline y verificación real pendiente por falta de credenciales; actualizar estado README.
- [ ] Repetir tests y gate completo con Postgres; revisar `git diff` contra este plan, pedir una revisión independiente y corregir hallazgos importantes con RED→GREEN. Registrar evidencia y commit.

## Cierre

No ejecutar verificaciones contra proveedores reales sin credenciales suministradas; registrar esos checks como bloqueados, no aprobados. Mantener `fase-1c` local y sin push ni despliegue. La fase 1d continuará AWS, Sentry, GitHub, entrega y MCP.
