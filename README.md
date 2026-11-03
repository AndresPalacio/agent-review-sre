# fin-graph

Biblioteca reutilizable e independiente de workflows:
[bm-workflow: API, ejemplo offline y límites](packages/workflow/README.md).

Análisis financiero multiagente con evidencia, cartera y órdenes paper, construido de
forma nativa sobre LangGraph. El repositorio está migrando desde su dominio SRE original;
los documentos de las fases 0–8 sin prefijo `F` conservan ese historial técnico.
Diseño completo: [docs/specs/2026-10-07-fin-graph-design.md](docs/specs/2026-10-07-fin-graph-design.md).
Guía de los patrones de agentes y del recorrido de una pregunta:
[docs/arquitectura-agentes.md](docs/arquitectura-agentes.md).

Piloto optativo con supervisor, hijos asíncronos, Decisions y MCP:
[panel ejecutable y guía Run/Debug](docs/arquitecture-review/2026-10-22-piloto-async-ejecutable.md).
Arranca con `.venv\Scripts\python.exe examples/demo_supervisor_async.py --serve`;
panel en `http://127.0.0.1:8012`, modelo offline y datos sintéticos explícitos.

Los análisis del producto se ejecutan por defecto mediante el
[runtime del harness](docs/better-harness.md#ejecución-predeterminada-del-producto):
CLI, chat, API, señales, gateways, tareas programadas y Studio comparten
configuración, identidad de variante y trazas. `uv run fin-graph harness runtime`
muestra la variante instalada y sus límites. La optimización train/holdout/scorecard
es una operación explícita aparte; la ejecución habitual no requiere el extra `harness`.

> Estado de la transformación financiera: F0–F6 implementadas y validadas localmente.
> CLI, grafos, API, señales, órdenes paper, gateways y scheduler ya usan contratos
> financieros. La consola web heredada se migra en F8 y los módulos SRE restantes se
> retiran en F9. La validación con LLM y proveedores reales requiere credenciales.

[Validación de fase 7](docs/progress/2026-10-08-fase-7.md) ·
[Cierre y límites de fase 8](docs/progress/2026-10-08-fase-8.md).

Los once hallazgos de congruencia previos a fase 7 están corregidos y cubiertos
por regresiones: [cierre de fase 6](docs/progress/2026-10-08-cierre-fase-6.md).

El dominio de 1b normaliza alertas de ocho fuentes, construye ventanas y planes de
investigación, verifica diagnósticos y genera reportes para Markdown, Slack, Telegram y
PagerDuty. La fase 1c añade lectores de Prometheus, Alertmanager, Grafana, Loki,
Tempo, Mimir, Datadog y New Relic. Studio exporta el grafo de investigación.
[Cierre de 1b](docs/progress/2026-10-07-fase-1b.md) ·
[Plan de 1c](docs/superpowers/plans/2026-10-07-fase-1c-observabilidad.md) ·
[Validación de 1c](docs/progress/2026-10-07-fase-1c.md).

La fase 1d añade lectores de AWS, Sentry y GitHub, entrega saliente por Slack,
Telegram y PagerDuty, y un loader MCP con política explícita por tool.
[Plan de 1d](docs/superpowers/plans/2026-10-07-fase-1d-cloud-entrega-mcp.md) ·
[Validación de 1d](docs/progress/2026-10-07-fase-1d.md).

La fase 2 conecta esas capacidades en un grafo RCA con especialistas en paralelo,
verificación de claims y refinamiento acotado. [Plan de 2](docs/superpowers/plans/2026-10-07-fase-2-investigacion-cli.md) ·
[Validación de 2](docs/progress/2026-10-07-fase-2.md).

La fase 3 añade un dataset de 36 escenarios EKS/RDS, clientes falsos que sirven su
evidencia a las tools reales y scores de categoría, palabras clave, fuentes, citas,
trayectoria, olas, latencia y tokens (cuando el proveedor los reporta).
[Plan de 3](docs/superpowers/plans/2026-10-07-fase-3-evaluacion.md) ·
[Validación de 3](docs/progress/2026-10-07-fase-3.md).

La fase 4 sirve investigaciones por API, guarda estado y eventos en Postgres, expone
SSE y recibe las ocho fuentes de alertas con autenticación y deduplicación.
[Plan de 4](docs/superpowers/plans/2026-10-07-fase-4-api-webhooks.md) ·
[Validación de 4](docs/progress/2026-10-07-fase-4.md).

La fase 5 incorpora acciones permitidas por catálogo, dry-run y aprobación
`approve/edit/reject`, verificación y propuesta de rollback. El chat usa especialistas,
memoria de incidentes y fuentes confiables; `ask` y `chat` funcionan con SQLite local o
la API. [Plan de 5](docs/superpowers/plans/2026-10-07-fase-5-hitl-chat-memoria.md) ·
[Validación de 5](docs/progress/2026-10-07-fase-5.md).

La fase 6 añade schedules de health checks Prometheus y digest con APScheduler
persistido en Postgres y un único líder mediante advisory lock. También programa
retención, rechazo de aprobaciones vencidas y gateways Slack/Telegram con botones
de aprobación vinculados a checkpoints. [Plan de 6](docs/superpowers/plans/2026-10-08-fase-6-scheduler-gateways.md) ·
[Validación de 6](docs/progress/2026-10-08-fase-6.md).

## Requisitos

- Python 3.12 y [uv](https://docs.astral.sh/uv/)
- Docker Desktop (solo para Postgres)
- Node.js 22.12+ y npm para desarrollar o compilar la UI

## Consola web (legada hasta F8)

La fase 7 original añadió `web/` (React, Vite, TypeScript y Tailwind). Esa consola aún
usa los contratos SRE antiguos y no es compatible con la API financiera de F6; su
migración forma parte de F8. Las instrucciones siguientes documentan la consola
legada mientras se completa esa fase.
La consola incluye investigaciones con progreso en vivo, evidencia e informe;
chat; preparación y aprobación de remediaciones; automatizaciones e integraciones.
El rol de la API key determina las operaciones disponibles. La clave se mantiene
en memoria: al recargar la página o cerrar sesión debes introducirla de nuevo.

Para desarrollo, inicia la API con `uv run fin-graph serve` y, en otra terminal:

```powershell
cd web
npm ci
npm run dev
```

Abre `http://127.0.0.1:5173`. Vite envía `/v1` a `127.0.0.1:8000`; las peticiones
SSE usan `fetch` con `X-API-Key`. Usa una clave provisionada por el servidor; nunca
incluyas claves en variables `VITE_*` ni en URLs. El chat requiere un modelo
configurado y las acciones requieren sus integraciones, igual que en API/CLI.

Para servir todo desde FastAPI, ejecuta `npm run build` en `web/` antes de arrancar
el servidor. El build genera `src/fin_graph/server/static`, ignorado por Git. La
consola queda en `http://127.0.0.1:8000/`; `/v1`, `/docs` y health mantienen sus rutas.
Antes de crear un wheel, compila también la UI; `uv build --wheel` incluye esos
estáticos. `docker build -t fin-graph .` hace ambos pasos en una imagen multistage.

Las remediaciones se preparan por separado del RCA. Revisa los argumentos y el
dry-run antes de aprobar; la UI vincula la decisión al interrupt mostrado y exige
una nueva decisión si el grafo propone rollback. No convierte recomendaciones de
un informe en comandos ejecutables automáticamente.

Validación web (desde `web/`):

```powershell
npm run typecheck
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

Playwright inicia su propia API de fixtures y Vite en los puertos 8000 y 5173;
detén otros servidores que ocupen esos puertos. La prueba usa `FixtureBackend`,
grafos reales y una acción simulada; no llama a modelos ni proveedores externos.
En Linux CI se instala Chromium con `npx playwright install --with-deps chromium`.

## Inicio rápido

```bash
uv sync
cp .env.example .env    # completa ANTHROPIC_API_KEY para usar modelos reales
uv run pytest
```

`.env` debe estar en UTF-8. En PowerShell copia con `Copy-Item .env.example .env`; si lo
reescribes, usa `Set-Content -Encoding utf8`.

## Postgres local

```bash
docker compose up -d --wait
FIN_GRAPH_TEST_DATABASE_URL="postgresql://fin_graph:fin_graph@localhost:5433/fin_graph?connect_timeout=5" uv run pytest -m postgres
```

En PowerShell:

```powershell
$env:FIN_GRAPH_TEST_DATABASE_URL = "postgresql://fin_graph:fin_graph@localhost:5433/fin_graph?connect_timeout=5"
uv run pytest -m postgres
```

## LangGraph Studio (desarrollo)

```bash
LANGGRAPH_CLI_NO_ANALYTICS=1 uv run langgraph dev --no-browser
```

`LANGGRAPH_CLI_NO_ANALYTICS=1` evita que la CLI envíe estadísticas de uso (en PowerShell:
`$env:LANGGRAPH_CLI_NO_ANALYTICS = "1"` antes del comando).

Studio exporta `analysis`, el mismo grafo financiero del CLI, y mantiene
`investigation` como grafo SRE legado hasta F9; ninguno lleva checkpointer propio.
`http://127.0.0.1:2024/ok` responde `{"ok":true}` cuando el servidor está listo. Detén con Ctrl+C.

## Análisis financiero local

```powershell
uv run fin-graph analyze --text "analiza AAPL y MSFT"
uv run fin-graph analyze --file .\solicitud.json --propose
```

`--file` admite una solicitud JSON o texto UTF-8 (máximo 1 MiB). El comando normaliza
los tickers, consulta solo las fuentes de mercado habilitadas y produce un reporte con
evidencia, tesis, riesgos y decisiones. `--propose` crea hilos separados de órdenes
paper pendientes de aprobación; nunca opera una cuenta real. Un análisis verificado
guarda su tesis para consultas y evaluación posteriores.

## API local

La API requiere `DATABASE_URL` de Postgres y `BOOTSTRAP_ADMIN_KEY` aleatoria de al
menos 32 caracteres en `.env`. La primera arrancada ejecuta la migración Alembic y
crea el hash de la clave admin en `api_keys`. El valor secreto no se devuelve por la
API. Inicia con `uv run fin-graph serve`; escucha solo en `127.0.0.1:8000` por
defecto. `GET /v1/health` y `/v1/ready` son públicos; las rutas de consulta usan
`X-API-Key` y las de creación/reanudación requieren rol operator o superior. Un
administrador puede generar claves viewer/operator/approver/admin con
`POST /v1/api-keys`; el valor se devuelve una sola vez y se almacena únicamente su
hash. `BOOTSTRAP_ADMIN_KEY` permite la provisión inicial.

`POST /v1/analyses` recibe `{"text":"..."}` o `{"request":{...}}`.
Para entregar el reporte, añade `"destinations":{"slack":"C123"}` al cuerpo;
se admiten `slack`, `telegram` y `pagerduty` con destinos de 1 a 128 caracteres.
Devuelve `thread_id` y `run_id`; consulta el reporte en
`GET /v1/analyses/{thread_id}`, el progreso en
`GET /v1/threads/{thread_id}/stream` (SSE, con `Last-Event-ID`) y el historial en
`GET /v1/threads/{thread_id}/history`. Una corrida interrumpida se reanuda con
`POST /v1/threads/{thread_id}/resume`. Los eventos de progreso almacenan etapas,
estado, tareas por analista e IDs de evidencia, sin payload de señales ni contenido
de herramientas. `GET /v1/analyses/{thread_id}/explain` combina la topología
del grafo con los pasos observados y los resúmenes derivados del estado. Usa
`X-API-Key` con rol viewer o superior.
Cada corrida construye de nuevo los clientes de integración; el checkpoint guarda
el reporte.
El historial acepta `after_id` y `limit` (máximo 500) para paginar; SSE transmite
todos los lotes pendientes y permite reanudar desde `Last-Event-ID`.

`POST /v1/signals/{source}` admite `tradingview`, `price_alert` y `generic`. Cada
fuente necesita su propia entrada en `WEBHOOK_TOKENS` (header
`X-Fin-Webhook-Token`) o en `WEBHOOK_HMAC_SECRETS` (header
`X-Fin-Signature: sha256=<hex>` sobre el cuerpo bruto). Si hay HMAC configurado, no
se acepta el token. TradingView, que no permite headers arbitrarios, puede incluir
`"token"` en el JSON; el servidor lo elimina antes de analizar. Una señal repetida
dentro de `SIGNAL_DEDUPE_MINUTES` devuelve el hilo original sin iniciar otro análisis.

`GET /v1/integrations` muestra las integraciones registradas y
`POST /v1/integrations/verify` hace una lectura de conectividad con los clientes
configurados. `CORS_ORIGINS` es una lista de orígenes separados por coma. El
`Dockerfile` empaqueta la API, pero no ejecuta ni despliega infraestructura.
La UI financiera corresponde a F8. La validación con webhooks y credenciales de
proveedores reales está pendiente.

## Chat y órdenes paper

`uv run fin-graph ask "¿Cómo ves AAPL?"` hace una consulta local;
`uv run fin-graph chat` abre el REPL con `/new`, `/threads`, `/resume`,
`/analiza`, `/approve`, `/integrations`, `/cost` y `/exit`. Los hilos locales
se guardan en SQLite bajo el directorio de datos del usuario. Para usar la API,
añade `--server https://servidor.example --api-key <clave>` o configura
`FIN_GRAPH_API_KEY`; `--thread-id` retoma un hilo remoto. El REPL muestra la
solicitud de aprobación antes de aceptar `/approve approve|reject|edit`.
Si la solicitud contiene varias acciones, `approve` y `reject` resuelven todo el
lote mostrado. `edit` requiere una sola acción pendiente; la edición de lotes se
rechaza antes de ejecutar cambios.

La API expone `POST /v1/chat/threads/{id}/messages` como SSE,
`GET /v1/chat/threads/{id}` para recuperar una aprobación pendiente y
`POST /v1/chat/threads/{id}/approve`. Para proponer una orden explícita usa
`POST /v1/orders` con `{"ticker":"AAPL","action":"buy","quantity":2}`;
`GET /v1/orders/{id}` muestra su estado y `POST /v1/orders/{id}/approve` reanuda con
`{"decisions":[{"type":"approve"}]}`. `edit` recibe `edited_action` con el mismo
nombre de acción y solo una cantidad positiva nueva; se valida y prepara de nuevo.
Solo una API
key de rol approver/admin puede decidir; `APPROVER_IDS` restringe opcionalmente
los aprobadores por hash SHA-256 de su clave. En producción toda mutación requiere
aprobación. `GET /v1/portfolio`, `GET /v1/orders`, `GET /v1/orders/pending`,
`POST /v1/orders/settle` y `POST /v1/orders/{client_order_id}/cancel` operan el libro
paper persistido. Las decisiones se auditan con actor,
tipo y hash de argumentos, sin almacenar los argumentos originales.

El grafo de órdenes vuelve a comprobar precio, horario de mercado, efectivo,
posición y límites antes de llenar. Fuera de sesión una orden aprobada queda en cola;
si el precio deriva demasiado se rechaza. El SSE de chat emite mensajes y eventos de
aprobación; la expiración automática de aprobaciones la ejecuta el scheduler.
`EMBEDDINGS_PROVIDER=openai` activa la búsqueda semántica en Postgres con pgvector;
sin él, la memoria busca por metadatos y texto. El contexto recordado orienta el
análisis, pero no cuenta como evidencia del mercado actual.

## Scheduler y bots (fase 6)

El servidor Postgres inicia APScheduler con un único líder por advisory lock. Las
definiciones se guardan en `schedules` y comienzan deshabilitadas. `market_watch`
evalúa movimiento diario, drawdown y picos de volatilidad y, si disparan, inicia un análisis
deduplicado; `portfolio_digest`, `thesis_review` y `watch_digest` son resúmenes
diarios de solo lectura. Ningún schedule coloca órdenes. Retención y rechazo de
aprobaciones vencidas (`APPROVAL_TTL_HOURS`, 24 por defecto) son jobs internos.
El scheduler detiene sus jobs si pierde la conexión que mantiene el lock y los
standby intentan adquirir el liderazgo automáticamente. Los eventos SSE usan
LISTEN/NOTIFY y replay durable; una notificación perdida se recupera al releer
Postgres. `MAX_PENDING_RUNS`, `MAX_HTTP_REQUESTS` y `MAX_STREAMS` limitan la carga
por réplica y devuelven 429 con `Retry-After` al alcanzar la capacidad. El disparo
manual de un schedule debe dirigirse al líder; un standby devuelve 503.
Los advisory locks usan un pool separado de hasta `MAX_CONCURRENT_RUNS + 8`
conexiones por réplica, además del ledger, checkpoints, Store, listener y scheduler.
Dimensiona `max_connections` de Postgres al aumentar concurrencia o réplicas;
los pools tienen capacidad y espera finitas.

La API ofrece `GET/POST /v1/schedules`, `GET/PUT/DELETE /v1/schedules/{id}` y
`POST /v1/schedules/{id}/run`. Crear, editar y borrar requieren rol admin;
el disparo manual requiere operator. La CLI usa la misma API y exige HTTPS fuera
de loopback:

```powershell
uv run fin-graph schedules list --server http://localhost:8000 --api-key <clave>
uv run fin-graph schedules create .\schedule.json --server http://localhost:8000 --api-key <clave>
uv run fin-graph schedules run aapl-watch --server http://localhost:8000 --api-key <clave>
```

Un `schedule.json` de mercado contiene, por ejemplo,
`{"id":"aapl-watch","enabled":false,"definition":{"kind":"market_watch","interval_minutes":5,"rules":[{"ticker":"AAPL","max_daily_move_pct":3,"max_drawdown_pct":15,"volatility_spike_ratio":1.8}]}}`.
Para tareas diarias se usan `kind=portfolio_digest|thesis_review|watch_digest`,
`hour`, `minute`, `timezone` IANA y destinos explícitos
`{"slack":"C...","telegram":"-100..."}`. Cada canal de entrega debe
estar configurado por separado; los envíos con resultado incierto quedan bloqueados
para evitar una repetición automática.

`SLACK_GATEWAY_*` habilita eventos HTTP firmados por Bolt; `SOCKET_MODE=true` y
`APP_TOKEN` activan Socket Mode optativo en desarrollo. `TELEGRAM_GATEWAY_*`
habilita el webhook con cabecera secreta. Ambos aceptan solicitudes financieras solo
del canal/chat configurado y decisiones de usuarios en su allowlist; los botones reanudan el
checkpoint bajo el mismo lock y auditoría de la API. El evento autenticado se guarda
en un inbox Postgres antes de confirmar el webhook. El líder reintenta pendientes
sin heartbeat; una solicitud reintentada reutiliza su hilo. Las credenciales de entrada
son independientes de `SLACK_DELIVERY_*` y `TELEGRAM_DELIVERY_*`. La comprobación
manual en un workspace sandbox queda pendiente de credenciales; no se hizo envío
a servicios externos durante las pruebas.

## Evaluación offline

Para optimizar el agente mediante el **better-harness oficial + Deep Agents**,
hay un flujo con **19 casos train, 10 holdout y 7 scorecard**, tres repeticiones
por caso y aceptación sin regresiones por caso holdout. Incluye nueve superficies
editables (prompts, selección de tools, skills, planificación y middleware), contratos
de llamadas y delegación, ejecución en copias temporales y exportación de cambios,
variantes, métricas, reportes y CI offline. Guía completa:
[better-harness de principio a fin](docs/better-harness.md).

El harness ya es el flujo predeterminado de investigación.
[Validación de la transición](docs/progress/2026-10-21-better-harness-full.md#cierre-de-la-tarea-7-2026-10-22).
La [revisión de arquitectura hacia Rovo](docs/arquitecture-review/2026-10-22-revision-rovo.md)
propone la evolución posterior con prioridades y criterios de aceptación.

```powershell
uv sync --locked --extra harness
uv run --extra harness fin-graph harness validate
uv run --extra harness fin-graph harness smoke
```

Smoke comprueba el funcionamiento del circuito; medir mejoras reales requiere
credenciales del modelo. Ninguna variante se despliega automáticamente.

```powershell
uv run fin-graph eval run --suite smoke --model scripted --output-dir .eval-results
```

El smoke ejecuta el grafo y una lectura real de tool sobre cada uno de tres escenarios,
sin credenciales, red, entregas ni mutaciones. `results.json` y `report.md` se escriben
en el directorio indicado. `--suite all --model scripted` comprueba los 36 escenarios
sin credenciales y **no mide la calidad RCA**: su diagnóstico es `unknown`
deliberadamente. La suite completa también corre en CI.

Para medir el modelo, configura la variable de credenciales del proveedor y ejecuta,
por ejemplo, `uv run fin-graph eval run --suite all --model provider:model-name
--repeat 3 --output-dir .eval-results`. `eks` tiene 14 casos, `rds` 22 y `all` 36.
Los runs con credenciales faltantes se registran como `blocked`; las fallas de un caso
no detienen el resto. La evaluación solo usa fixtures locales; no activa integraciones
externas. No genera baseline aprobada hasta que todos los casos terminen sin errores.
El juez LLM de referencia permanece opcional y sin ejecutar por defecto; se activa
con `--judge-model provider:model-name` en corridas reales y su score se reporta
por separado.

Puedes conservar un incidente real ya investigado con `eval record`, proporcionando
un `--answer-file` independiente y `--reviewer`. El formato versionado guarda
argumentos y resultados redactados. `eval replay` ejecuta el grafo contra esas
lecturas exactas: una llamada desconocida falla y no consulta integraciones reales.
`eval compare baseline.json current.json` rechaza resultados incompletos, scripted,
datasets distintos o regresiones. Consulta `uv run fin-graph eval --help` y la
[guía de aceptación](docs/phase8-acceptance.md).

## Integraciones

Cada integración se activa con sus variables en `.env`; las tools de investigación
son de solo lectura y las mutaciones de fase 5 se ejecutan solo tras aprobación. Kubernetes usa
`K8S_ENABLED=true` y, si no usas `~/.kube/config`, `K8S_KUBECONFIG` y `K8S_CONTEXT`.
Una integración mal configurada queda no disponible y la corrida sigue con las demás.

Para observabilidad, activa por separado `PROMETHEUS_`, `ALERTMANAGER_`, `GRAFANA_`,
`LOKI_`, `TEMPO_`, `MIMIR_`, `DATADOG_` o `NEWRELIC_` con sus variables de
[`.env.example`](.env.example). Cada endpoint ofrece sus tools solo cuando tiene un
cliente configurado. Los ocho hacen una lectura autenticada en `verify`; los permisos
pueden diferir por tool. La CLI permite listar, verificar y configurar todas las
integraciones nativas; los secretos se piden sin mostrarlos y la escritura de `.env`
es atómica. `--verify` comprueba las credenciales propuestas antes de guardar:

```powershell
uv run fin-graph integrations list
uv run fin-graph integrations verify
uv run fin-graph integrations setup prometheus --env-file .env --dry-run
uv run fin-graph integrations setup prometheus --env-file .env --verify
```

También están disponibles Opsgenie, incident.io, Jira, Honeycomb, Elasticsearch,
OpenSearch, Splunk, VictoriaLogs, Coralogix, Azure Log Analytics, ArgoCD, Helm y
GitLab. La [matriz de segunda ola](docs/integrations-wave2.md) detalla operaciones,
autenticación y límites. Sus pruebas usan transportes simulados, no cuentas reales.
Para usar el registro desde Python:

```python
import asyncio
from fin_graph.integrations.catalog import default_registry


async def main():
    registry = default_registry()
    context = registry.run_context()
    for result in await registry.verify_all(context):
        print(result.integration, result.status, result.detail)


asyncio.run(main())
```

Los tests de 1c usan respuestas HTTP simuladas. La verificación contra cuentas reales
queda pendiente de credenciales y endpoints. `NEWRELIC` usa el esquema experimental
`AiIssues` solo para issues e incidents; Datadog usa su API v1 de métricas para la
consulta de timeseries, mientras que logs, spans, eventos e incidents usan v2.

En 1d, `AWS_ENABLED` requiere una región y usa la cadena estándar de credenciales de
boto3; `SENTRY_ENABLED` requiere organización y token; `GITHUB_ENABLED` requiere
owner, repo y token. Se registran por separado. La lectura de Stacktrace de Sentry
usa el detalle de un evento. Las variables están en [`.env.example`](.env.example).

`DeliveryService.from_env()` configura los tres canales de entrega de forma
independiente. `verify_all()` consulta identidad sin enviar mensajes;
`send_report(report, destinations, idempotency_key)` hace los envíos explícitos.
Slack recibe un `client_msg_id` estable. Telegram y PagerDuty requieren deduplicación
durable, igual que Slack: el servidor inyecta la tabla de entregas por ejecución,
canal y destino. Un envío confirmado no se repite al reanudar; un resultado
incierto bloquea el reenvío automático. Otra ejecución del mismo fingerprint puede
enviar su propio reporte. No se registran envíos como tools de investigación.

Las señales usan `SIGNAL_DELIVERY_ROUTES`, un objeto JSON con rutas `default`,
`tradingview`, `price_alert` y `generic`. Una ruta por fuente sustituye `default`;
una ruta vacía desactiva la entrega para esa fuente. Por ejemplo,
`{"default":{"slack":"C123"},"tradingview":{"telegram":"42"}}`. Solo esta
configuración del operador decide los destinos; los campos del payload no pueden
cambiarlos. Los schedules usan `destinations` de su propia definición.

Para MCP, copia [`mcp-servers.example.json`](mcp-servers.example.json) a un archivo
local y mantén secretos en variables de entorno. `MCP_CONFIG` selecciona ese archivo;
si está vacío, se usa `mcp-servers.json` cuando existe. Servidor, CLI y Studio
descubren los servidores explícitamente habilitados. Las tools sin declaración son
`external` y quedan fuera de los especialistas de lectura; declara dominios y
permisos solo cuando conozcas su comportamiento. Las llamadas tienen deadline,
límite de salida y redacción de secretos. Una conexión fallida queda no disponible
sin impedir el uso de las demás integraciones.

## Seguridad

- Los secretos (tokens, llaves, contraseñas en URLs) se redactan en la salida de cada tool
  antes de que la vea el modelo o se guarde.
- `MASK_ENABLED=true` reemplaza identificadores (pods, namespaces, IPs...) por tokens
  estables antes de enviarlos al modelo; las tools siguen recibiendo los valores reales.
- Toda tool que cambia estado pide aprobación humana; `AUTO_APPROVE_TOOLS` la salta solo en
  entornos no productivos (`dev`, `development`, `local`, `test`, `staging`); cualquier
  otro valor de `ENVIRONMENT` cuenta como producción.

## Calidad

La aceptación offline de carga, recuperación y replay SSE se ejecuta con
`uv run python scripts/phase8_acceptance.py load`. La guía distingue esas pruebas
de la validación de modelos y proveedores, que requiere credenciales:
[aceptación de fase 8](docs/phase8-acceptance.md).

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && uv run pytest
```

## Licencia

Apache-2.0. `NOTICE` lista el material adaptado de OpenSRE y de LangGraph Starter Kit.
