# Fase 6: scheduler y gateways — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Ejecutar chequeos, digest, retención y vencimiento de aprobaciones, y operar alertas/aprobaciones por Slack y Telegram con autenticación y persistencia.

**Architecture:** APScheduler 3.x persiste trabajos y opera solo bajo un advisory lock Postgres. Un grafo de lectura produce resultados de salud; el scheduler escala anomalías por RunManager. Los gateways validan eventos, persisten su vínculo remoto con hilos y reutilizan el flujo HITL existente.

**Tech Stack:** Python 3.12, LangGraph 1.2, APScheduler 3.x, SQLAlchemy/Postgres, Slack Bolt async, FastAPI, httpx, pytest.

**Spec:** `docs/superpowers/specs/2026-10-08-fase-6-scheduler-gateways-design.md` y `docs/specs/2026-10-07-sre-graph-design.md`.

## Global Constraints

- Rama normal `fase-6`; sin worktree nuevo, push, despliegue ni infraestructura real.
- Ningún evento entrante concede identidad de aprobador por sí solo: allowlist exacta por plataforma.
- Mutaciones únicamente a través de aprobación fase 5; el scheduler solo puede rechazar solicitudes vencidas.
- Jobs persistidos no contienen secretos, objetos del servidor ni payloads crudos.
- Configuración deshabilitada por defecto; validación externa manual queda pendiente.

## Review Focus

- Dos procesos inician a la vez: solo uno ejecuta trabajos; el perdedor sigue sirviendo API.
- Evento repetido o pulsación doble: no crea dos investigaciones ni ejecuta dos acciones.
- Firma Slack o secreto Telegram incorrectos: rechazo antes de side effects.
- Aprobación vencida mientras se aprueba manualmente: el lock y la revalidación hacen ganar una sola decisión.
- Query sin datos, NaN o error de vendor: estado `unknown`, nunca `healthy`.

---

### Task 1: Grafo y persistencia de health checks

**Files:** crear `src/sre_graph/graphs/health_check/{__init__,graph}.py`, `tests/graphs/health_check/test_graph.py`; modificar `src/sre_graph/server/storage.py` y añadir migración `0006_phase6.py`.

**Interfaces:** `HealthCheckDefinition`, `HealthCheckResult`, `make_health_check_graph(probe, model=None)`; `Storage.record_health_result(...)`, `recent_health_results(...)`, `put_schedule(...)`, `list_schedules(...)`, `delete_schedule(...)`.

- [x] Escribir tests de umbral, datos ausentes/NaN, error, escalada y digest; confirmar RED.
- [x] Implementar el grafo y almacenamiento de resultados/schedules; confirmar GREEN.
- [x] Ejecutar Ruff y mypy en los módulos y pruebas focalizadas; commit.

### Task 2: Scheduler líder y jobs

**Files:** crear `src/sre_graph/scheduler/{__init__,service,jobs}.py`, `tests/scheduler/test_service.py`; modificar configuración y lockfile.

**Interfaces:** `SchedulerService.start()/stop()/sync_schedule()`, `run_scheduled_job(schedule_id)`; Postgres advisory lock, APScheduler SQLAlchemyJobStore, jobs internos de retención y expiry.

- [x] Tests con reloj/DB simulados: líder único, jobs serializables, reconciliación, stop y dedupe; confirmar RED.
- [x] Implementar y confirmar GREEN; integrar RunManager para escalar anomalías en hilo propio.
- [x] Ejecutar pruebas focalizadas, Ruff y mypy; commit.

### Task 3: Vencimiento de aprobaciones

**Files:** crear `src/sre_graph/scheduler/expiry.py`, `tests/scheduler/test_expiry.py`; modificar `src/sre_graph/graphs/remediation/graph.py`, `src/sre_graph/server/storage.py` y `src/sre_graph/server/app.py`.

**Interfaces:** `expire_pending_approvals(...)` con locks, reloj inyectable y decisión `reject`; actor interno limitado a rechazo.

- [x] Tests de vencida/no vencida, carrera con aprobación y auditoría; confirmar RED.
- [x] Implementar y confirmar GREEN sin ruta externa para el actor interno.
- [x] Ejecutar pruebas focalizadas y checks estáticos; commit.

### Task 4: Gateway Slack

**Files:** crear `src/sre_graph/gateway/{__init__,slack,settings}.py`, `tests/gateway/test_slack.py`; ampliar `delivery/client.py` solo para editar mensaje.

**Interfaces:** `SlackGateway` procesa `message` y `action`, usa `AsyncApp` y firma Bolt; HTTP y Socket Mode optativo.

- [x] Tests ASGI/Bolt de firma, ack, allowlists, dedupe, vínculo hilo, progreso/reporte y botón; confirmar RED.
- [x] Implementar y confirmar GREEN con Web API simulada.
- [x] Ejecutar pruebas focalizadas y checks estáticos; commit.

### Task 5: Gateway Telegram

**Files:** crear `src/sre_graph/gateway/telegram.py`, `tests/gateway/test_telegram.py`; ampliar `delivery/client.py` para editar mensaje y responder callback.

**Interfaces:** `TelegramGateway.handle(update, secret)`, validación de webhook, inline keyboard y correlación persistida.

- [x] Tests de secreto, update duplicado, allowlist, progreso/reporte, callback y rechazo; confirmar RED.
- [x] Implementar y confirmar GREEN con MockTransport.
- [x] Ejecutar pruebas focalizadas y checks estáticos; commit.

### Task 6: Superficie compartida, API, CLI y lifespan

**Files:** modificar `src/sre_graph/server/{app,config}.py`, `src/sre_graph/cli/main.py`, `tests/server/test_scheduler_api.py`, `tests/server/test_gateway_api.py`, `tests/cli/test_schedules.py`.

**Interfaces:** `/v1/schedules` CRUD/trigger, `/v1/gateway/slack`, `/v1/gateway/telegram`; `sre-graph schedules`; shared approval adapter para bots/API.

- [x] Tests de autenticación, validación, rutas, estados, lifecycle sin credenciales y CLI; confirmar RED.
- [x] Conectar scheduler y gateways al servidor; confirmar GREEN.
- [x] Ejecutar pruebas focalizadas, Ruff, mypy e import-linter; commit.

### Task 7: Gate, revisión y cierre

**Files:** `README.md`, `.env.example`, `docs/progress/2026-10-08-fase-6.md`, ledger SDD.

- [x] Ejecutar pytest completo con Postgres local obligatorio si está disponible, Ruff check/format, mypy, import-linter, smoke y build.
- [x] Revisar el diff contra ambos diseños y corregir hallazgos importantes.
- [x] Documentar pruebas ejecutadas/omitidas/bloqueadas y validación externa pendiente.
- [x] Commit, merge local no destructivo a `main` y verificación final; sin push.
