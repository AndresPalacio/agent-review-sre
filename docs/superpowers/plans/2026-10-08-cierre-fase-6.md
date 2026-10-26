# Correcciones de cierre de fase 6

> **For agentic workers:** Use superpowers:subagent-driven-development or
> superpowers:executing-plans. Cada bloque requiere reproducción roja, corrección,
> comprobación verde y revisión independiente.

**Goal:** Resolver los once hallazgos de la revisión de congruencia antes de fase 7.

**Architecture:** Conservar LangGraph nativo y los contratos existentes. Las
decisiones de bots se vinculan al interrupt concreto; los lectores conservan
semántica de polling y paginación; RCA y chat verifican todas sus fronteras.

**Tech Stack:** Python 3.12, LangGraph/LangChain fijados en uv.lock, FastAPI,
SQLAlchemy, Postgres, pytest.

**Spec:** [Diseño base](../../specs/2026-10-07-fin-graph-design.md) y
[fase 6](../specs/2026-10-08-fase-6-scheduler-gateways-design.md).

## Restricciones

- Autorización: el usuario pidió corregir los once hallazgos antes de fase 7.
- No implementar UI ni alterar el alcance de remediación explícita aceptado en fase 5.
- No contactar vendors ni utilizar credenciales reales durante las pruebas.
- Conservar `.idea/` y el worktree previo; rama local `fix/cierre-fase-6`.
- No añadir dependencias, secretos al estado, ni debilitar guardrails.
- Las pruebas locales parten de 792 aprobadas, incluyendo siete de Postgres.

## Focos de revisión

- Botón duplicado cuando el primer approve produce otro interrupt: no autoriza la nueva acción.
- Datos de herramientas enviados al especialista: masking activo en cada llamada al modelo.
- Resultado AWS transitorio y cursor operativo: completar polling/paginación sin exponer credenciales.
- Consultas vacías y fechas históricas: ampliar ventanas útiles y evaluar datos del escenario.
- Destinos de una alerta automática: persistirlos y deduplicar entregas al reanudar.

## Tarea 1 — RCA y evaluación (hallazgos 2, 9, 11)

**Files:** `graphs/investigation/graph.py`, `domain/verification.py`,
`evals/runner.py`, `evals/scenarios.py`, pruebas de investigación/evals.

**Interfaces:** Conservar construcción del grafo e inyección de reloj; extender
metadatos del escenario sólo si hace falta. No modificar superficies.

- [x] Añadir regresiones: claim WEAK con evidencia no relacionada llama al juez y
  no aprueba; respuestas vacías ensanchan ventana; métricas fixture caen dentro
  de la ventana entregada al modelo en la evaluación no scripted.
- [x] Ejecutar pruebas y observar los fallos esperados.
- [x] Corregir verificación de claims, detección semántica de evidencia vacía y
  reloj/fecha de escenario reproducible, sin leer respuestas esperadas para inferir RCA.
- [x] Ejecutar tests de dominio/investigación/evals y controles de tipos/formato focalizados.
- [x] Revisar diff contra los tres hallazgos.

## Tarea 2 — Chat, CLI y recuperación K8s (hallazgos 3, 4, 10)

**Files:** `graphs/chat/graph.py`, `cli/chat.py`,
`integrations/kubernetes/client.py`, pruebas correspondientes.

**Interfaces:** Conservar `make_chat_graph`; cada especialista usa masking con
el mismo contrato reversible. CLI approve/reject decide todo el lote presentado;
edit conserva el contrato individual y rechaza lotes antes de auditar o reanudar.

- [x] Añadir regresiones: datos IP de tools no llegan crudos al modelo especialista;
  generaciones sin observar no verifican restart/undo; dos acciones se pueden
  aprobar y rechazar por CLI local y remoto.
- [x] Ejecutar y observar fallo de las nuevas pruebas.
- [x] Aplicar middleware al especialista, comprobar observed_generation, construir
  tantas decisiones CLI como acciones de la solicitud visible.
- [x] Ejecutar tests de chat/CLI/K8s/remediación y controles focalizados.
- [x] Revisar seguridad de argumentos editados y de masking reversible.

## Tarea 3 — AWS y cursores (hallazgos 7, 8)

**Files:** `integrations/aws/*`, `agents/middleware/tool_cache.py` si es necesario,
`safety/secrets.py`, pruebas AWS/middleware/secretos.

**Interfaces:** Mantener tools AWS y metadata; no cachear resultados en progreso.
Los cursores operativos de respuestas confiables deben seguir utilizables sin
permitir que credenciales disimuladas como token escapen de los filtros.

- [x] Añadir regresiones de agente/tool real con Running→Complete y NextToken
  atravesando payload, guardrails y llamada a segunda página.
- [x] Confirmar fallos antes de cambiar implementación.
- [x] Corregir polling acotado o política de caché y tratamiento específico de cursores.
- [x] Ejecutar pruebas AWS/guardrails/caché/secretos y controles focalizados.
- [x] Revisar que tokens de autenticación siguen redactados.

## Tarea 4 — Gateways y entrega (hallazgos 1, 5, 6)

**Files:** `gateway/*`, `server/app.py`, `server/manager.py`,
`server/storage.py`, migración si necesaria, `scheduler/jobs.py`, configuración,
pruebas de servidor/gateway/scheduler/entrega.

**Interfaces:** Vincular notificaciones a interrupt exacto en persistencia o
token autenticado; comparar bajo el lock existente antes de reanudar. Añadir
destinos explícitos de API y configuración de routing de alertas por severidad;
propagarlos al grafo y guardar deduplicación durable de reportes.

- [x] Añadir pruebas de botón viejo después de nuevo interrupt y después de
  aprobación por API, para Slack/Telegram; propia identidad bot ignorada, otros
  bots de alertas aceptados; schedule/API/webhook producen entrega configurada.
- [x] Observar fallos esperados.
- [x] Implementar vínculo de aprobación, filtro selectivo y propagación de destinos.
- [x] Verificar replay sin envío duplicado y sin aprobar otra solicitud.
- [x] Ejecutar tests server/gateway/scheduler/delivery y Postgres.
- [x] Revisar rutas completas y migración empaquetada, si aplica.

## Cierre

- [x] Revisión independiente de los cuatro bloques y corrección de observaciones.
- [x] `uv run --locked ruff check .` y `uv run --locked ruff format --check .`.
- [x] `uv run --locked mypy` y `uv run --locked lint-imports`.
- [x] Suite completa con `FIN_GRAPH_REQUIRE_POSTGRES=1` y Postgres local.
- [x] Smoke offline de evaluación, construcción del wheel y documentación de resultados.
- [x] Inspeccionar diff/status; entregar cambios sin push ni despliegue.
