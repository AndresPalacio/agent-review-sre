# Better-harness Full Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline with a final independent review.

**Goal:** Evaluate actual tool/agent execution and optimize production harness code as well as prompts.
**Architecture:** Immutable tracing/grading wraps the existing graph. Upstream variants are
evaluated in copied source workspaces; the official Deep Agent proposes allowed files.
**Tech Stack:** Python, LangGraph, LangChain, upstream better-harness, pytest, uv.
**Spec:** docs/superpowers/specs/2026-10-21-better-harness-full-design.md

## Constraints
Preserve existing dirty work and projects/. No publishing, deployment or automatic adoption.
All guards and graders remain outside editable surfaces. Keep frozen family splits.
Do not claim provider-backed quality from scripted or mocked tests.

## Tasks
- [x] 1. RED/GREEN: service/tool traces with parent identity, errors, redaction and persisted partial runs.
  Files: evals/trace.py, evals/runner.py, tests/harness/test_trace.py.
- [x] 2. RED/GREEN: typed trajectory contracts, source-derived mandatory calls, agent ownership,
  argument rules, dependency order, budgets; quality gate and train feedback.
  Files: evals/trajectory.py, harness/quality.py, tests/harness/test_trajectory.py.
- [x] 3. RED/GREEN: production extension modules for tools, skills, planning, middleware and
  registration; fixed safety envelope. Generalize surface allowlist and config to workspace_file.
  Files: graphs/investigation/strategy/, services.py, harness/surfaces.py, configuration.py, sre.toml.
- [x] 4. RED/GREEN: source-copy subprocess runner and timeout/error handling; no checkout mutation.
  Files: harness/worker.py, execution.py, runner.py, tests/harness/test_execution.py.
- [x] 5. RED/GREEN: budget gates, provenance, train-only traces, inspect/export commands and
  upstream compatibility; integration and docs/CI.
- [x] 6. Full verification and independent review; resolve material defects and record evidence.

## Continuación solicitada por el usuario

- [x] 7. **Convertir el harness en el flujo principal y predeterminado de ejecución de todo el proyecto.**
  Estado: implementada y verificada localmente el 2026-10-22. Prioridad: alta.
  La integración actual permite evaluar y optimizar el agente; esta tarea debe unificar
  la ejecución habitual del producto alrededor del runtime y la configuración del harness.

  Criterios de aceptación:
  - Todos los puntos de entrada de investigación (CLI, API/UI, webhooks y tareas en
    segundo plano que correspondan) utilizan el mismo flujo del harness.
  - La configuración y la variante activa se resuelven en un único lugar, con trazas,
    contratos de tools/agentes y presupuestos consistentes.
  - Se mantienen las capacidades existentes de seguridad, persistencia, streaming,
    cancelación, recuperación de errores y control de efectos externos.
  - Se distingue la ejecución de incidentes de la optimización train/holdout/scorecard,
    evitando iniciar una campaña de optimización por cada petición del producto.
  - Los comandos predeterminados, la documentación y CI reflejan el nuevo flujo;
    las rutas anteriores quedan migradas o como compatibilidad explícita.
  - Pruebas de integración de los puntos de entrada y evals train/holdout/scorecard
    demuestran que la transición conserva el comportamiento y no introduce regresiones.

  Evidencia, decisiones y límites en el [registro de progreso](../../progress/2026-10-21-better-harness-full.md#cierre-de-la-tarea-7-2026-10-22).

- [x] 8. **Revisión final de arquitectura con referencia a Rovo.**
  Solicitada por el usuario el 2026-10-22. Ejecutar después de finalizar la tarea 7
  y sus verificaciones, como última tarea de este plan.
  - Revisar `docs/arquitecture-review/sre-graph-arquitectura-multiagente.pdf` y contrastar
    sus propuestas con el código actual, incluido el runtime común del harness.
  - Consultar documentación oficial vigente de Atlassian Rovo y distinguir las
    capacidades documentadas de las inferencias sobre su arquitectura interna.
  - Identificar brechas y mejoras para aproximarse a esa arquitectura, con prioridades,
    componentes afectados, dependencias y criterios de aceptación/evaluación.
  - Guardar la revisión y una propuesta de evolución en `docs/arquitecture-review/`;
    esta tarea es de análisis y no autoriza por sí sola una reescritura del producto.

  Completada el 2026-10-22, después del cierre de la tarea 7. El
  [informe de revisión](../../arquitecture-review/2026-10-22-revision-rovo.md) contrasta
  el PDF y el código con fuentes oficiales y propone entregas con dependencias,
  criterios de aceptación y evals. No se implementó la arquitectura futura.

## Review focus
Parallel callbacks must not mix specialists. Failed tool output cannot satisfy required calls.
Invalid or timed-out Python candidates cannot corrupt source or obtain a successful score.
Editable middleware cannot remove the fixed guard stack. Private feedback cannot enter proposals.
