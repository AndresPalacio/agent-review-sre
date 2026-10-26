# Fase 1d — Cloud, cambios, entrega y MCP

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** completar la ola 1 de capacidades: lecturas AWS, Sentry y GitHub, entrega Slack/Telegram/PagerDuty y carga explícita de herramientas MCP.

**Architecture:** AWS usa boto3 en `asyncio.to_thread`; Sentry y GitHub usan el transporte HTTP acotado de 1c. Cada lector conserva settings, cliente, tools con `ToolMeta` y `verify` en el registro explícito. Entrega es una capacidad saliente separada de las tools de investigación; el loader MCP acepta solo servidores declarados en JSON y asigna dominios y nivel de efecto antes de devolver herramientas.

**Tech Stack:** Python 3.12, boto3/botocore, httpx 0.28, pydantic-settings, langchain-mcp-adapters, pytest, uv.

**Spec:** `docs/specs/2026-10-07-fin-graph-design.md`, secciones Integraciones ola 1, Seguridad y Fase 1.

## Global Constraints

- Trabajar en la rama local `fase-1d` del directorio principal, sin worktree, push, despliegue ni cambios en infraestructura real. Conservar `.idea/`.
- Las tools AWS/Sentry/GitHub son de solo lectura. Entrega solo se ejecuta al invocar explícitamente su API; no se envían mensajes en tests ni en `verify`.
- Settings desactivados por defecto; secretos únicamente en clientes del `RunContext`, nunca en estado, args de tools, salida o diagnósticos.
- Cada llamada externa tiene timeout, páginas/ventanas acotadas y errores sin body, URL con credenciales ni headers. Validar segmentos de ruta suministrados por usuarios.
- `ToolMeta` declara integración, dominio, tipo de evidencia y nivel de efecto; las herramientas MCP son `external` salvo clasificación explícita.
- Gate: Ruff, formato, mypy, import-linter, pytest completo con Postgres local si está disponible; clientes de proveedores se prueban sin red.

## Decisiones y ajustes al diseño

- AWS se registra como un solo ID con región/perfil y SDK estándar; la cadena de credenciales de AWS permanece fuera de `.env`. `verify` consulta CloudWatch alarms, que comprueba autorización de lectura además de autenticación.
- Sentry y GitHub exponen una página y su cursor o número de página; no iteran ilimitadamente. `HttpJsonClient` debe poder devolver la cabecera `Link` sin revelar otras cabeceras.
- `verify` de entrega solo autentica/consulta identidad; enviar un reporte es una llamada separada. Las claves de idempotencia se propagan cuando el proveedor las soporta; la deduplicación durable de entregas pertenece a las tablas de la Fase 4.
- El loader MCP no arranca servidores por descubrimiento ni carga herramientas al importar. Su configuración declara transporte, nombre y política por tool. La conexión sucede al invocarlo explícitamente. El servidor de producción lo incorporará a su lifespan en Fase 4.

## Review Focus

1. Error 200 con sobre `ok=false` o `errors`: rechazarlo sin exponer el mensaje remoto en `test_delivery_error_envelopes` y `test_sentry_github_error_envelopes`.
2. Segmentos de ruta con `/`, `..` o URL: rechazo previo a red en `test_vendor_path_validation`.
3. Paginación incompleta: devolver `nextCursor`/`next_page` en `test_sentry_github_pagination`.
4. Consulta AWS que bloquea o devuelve páginas masivas: timeout y límite de resultados en `test_aws_bounded_reads`.
5. Tool MCP no clasificada o config con secreto literal: nivel `external` y error de configuración en `test_mcp_policy_defaults`.

## Mapa de archivos

| Ruta | Responsabilidad |
|---|---|
| `integrations/aws/{settings,client,tools,integration}.py` | CloudWatch métricas/logs/alarms, EKS, RDS/PI, Lambda y CloudTrail; SDK sincrónico aislado |
| `integrations/sentry/{settings,client,tools,integration}.py` | Issues, detalle/eventos, releases |
| `integrations/github/{settings,client,tools,integration}.py` | Deployments, commits, PRs, workflow runs, compare |
| `integrations/http.py` | Exponer `Link` de respuestas JSON acotadas |
| `delivery/{settings,client,service}.py` | Clientes salientes y coordinación de reporte renderizado/redactado |
| `integrations/mcp/{config,loader}.py` | Configuración JSON validada, adaptadores y política de tools |
| `integrations/catalog.py`, `.env.example`, `README.md`, `docs/progress/` | Registro, configuración y evidencia |

Fuentes de contrato: [boto3 CloudWatch Logs](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/logs/client/start_query.html), [Sentry API](https://docs.sentry.io/api/), [GitHub REST](https://docs.github.com/en/rest), [Slack chat.postMessage](https://docs.slack.dev/reference/methods/chat.postmessage), [Telegram Bot API](https://core.telegram.org/bots/api), [PagerDuty API](https://developer.pagerduty.com/api-reference/), [LangChain MCP adapters](https://reference.langchain.com/python/langchain-mcp-adapters/client/MultiServerMCPClient).

---

### Task 1: AWS de solo lectura

**Files:** `pyproject.toml`, `uv.lock`, `integrations/aws/{settings,client,tools,integration}.py`, `integrations/catalog.py`, `tests/integrations/test_aws.py`.

**Interfaces:** `AwsClient(session, region, timeout_seconds).read(service, operation, **kwargs) -> dict`; métodos tipados para `metric_data`, `alarms`, `logs_start`, `logs_results`, `eks_cluster`, `rds_instance`, `rds_events`, `rds_performance`, `lambda_function`, `cloudtrail_events`; `AwsIntegration` publica tools de esos métodos.

- [ ] Crear tests de settings desactivados/región, STS/CloudWatch verify, parámetros SDK, límites de ventana/página, error de permisos y metadata de tools. Usar clientes SDK falsos o Stubber, sin acceso real.
- [ ] Ejecutar tests nuevos y observar fallo por ausencia de módulo.
- [ ] Añadir boto3 y cliente async vía `asyncio.to_thread`; registrar AWS; añadir configuración y pasar tests focalizados.
- [ ] Ejecutar gate y commit `feat(integrations): add AWS read tools`.

### Task 2: Sentry y GitHub

**Files:** `integrations/http.py`, paquetes `sentry/`, `github/`, catálogo, `.env.example`, tests de ambos y de HTTP.

**Interfaces:** `HttpJsonClient.request_with_headers(...) -> tuple[Any, Mapping[str,str]]`; `SentryClient.issues/detail/events/releases`; `GitHubClient.deployments/commits/pulls/workflow_runs/compare`; cada listado devuelve una página más continuación.

- [ ] Escribir tests de token/settings, rutas, filtros temporales, paginación `Link`, errores 401/403/429, path traversal y `verify` autenticado.
- [ ] Ejecutar tests y observar fallos.
- [ ] Implementar el transporte de cabeceras y lectores; registrar IDs separados y tools por dominio.
- [ ] Gate y commits `feat(integrations): add Sentry reader`, `feat(integrations): add GitHub change reader`.

### Task 3: Entrega saliente

**Files:** `delivery/{settings,client,service}.py`, `.env.example`, tests `tests/delivery/`.

**Interfaces:** `DeliveryService.verify_all() -> list[VerifyResult]`; `send_report(report, destinations, idempotency_key) -> list[DeliveryResult]`; los clientes Slack/Telegram/PagerDuty reciben `httpx.MockTransport` en tests.

- [ ] Escribir tests para canales desactivados, `verify` sin enviar, payload renderizado y redactado, resultado/error de cada proveedor y clave de idempotencia cuando proceda.
- [ ] Ejecutar tests y observar fallo.
- [ ] Implementar clientes y servicio; no registrar envíos como tools ni iniciar entrega implícita.
- [ ] Gate y commit `feat(delivery): add Slack Telegram and PagerDuty senders`.

### Task 4: Loader MCP y cierre

**Files:** `pyproject.toml`, `uv.lock`, `integrations/mcp/{config,loader}.py`, `mcp-servers.example.json`, `.env.example`, `README.md`, `docs/progress/2026-10-07-fase-1d.md`, tests MCP y catálogo.

**Interfaces:** `load_mcp_config(path) -> McpConfig`; `async load_mcp_tools(config, client_factory=...) -> McpLoadResult` con tools etiquetadas y fallos por servidor; `verify_mcp` enumera tools por servidor sin invocarlas.

- [ ] Escribir tests de JSON inválido, servidor no configurado, transporte, env por nombre, nombres duplicados, clasificación default `external`, clasificación explícita y fallo aislado.
- [ ] Ejecutar tests y observar fallo.
- [ ] Añadir dependencia adaptador e implementar loader; actualizar documentación y progreso con verificaciones reales bloqueadas por falta de credenciales.
- [ ] Revisar contra diseño, repetir gate completo con Postgres, revisar diff y commit. Mantener rama local sin merge ni push salvo petición expresa.
