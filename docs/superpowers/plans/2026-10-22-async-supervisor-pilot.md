# Plan de implementación del supervisor asíncrono

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Ejecutar las tareas con TDD y revisión final; tareas independientes pueden investigarse en paralelo.

**Goal:** Entregar un piloto depurable de supervisor con hijos reales de Async Deep Agents, routing opcional Decisions e interfaces HTTP/MCP sobre el mismo servicio.

**Architecture:** Agent Server conserva la ejecución de supervisor e hijos. Un servicio local conserva comandos, identidad y revisiones, y utiliza el SDK para controlar el motor. El modo offline es explícito; el modo live usa el modelo configurado.

**Tech Stack:** Python 3.12, LangGraph, Deep Agents 0.7.29, LangGraph SDK 0.4.6, FastAPI, FastMCP, SQLite, OpenAI SDK 3.26.0.

**Spec:** [Diseño del piloto](../specs/2026-10-22-async-supervisor-pilot-design.md).

## Restricciones

- Trabajar sobre los cambios existentes sin descartarlos ni crear commits del trabajo ajeno.
- No modificar el comportamiento del chat/RCA de producción por defecto.
- Sin llamadas a proveedores reales si faltan credenciales; errores visibles y datos de prueba identificados.
- El gateway local es de un actor y un proceso; no prometer operación distribuida de su ledger.

## Revisión de fallos

- Command_id repetido con datos distintos: conflicto.
- Respuesta perdida después de crear run: reconciliar metadata antes de cualquier reintento.
- Actualización con revisión vieja: conflicto y ningún envío.
- Cancelación y llegada tardía de hijos: estado de negocio cancelado domina, reconciliar ejecuciones.
- Modelo propone hijos excesivos o tarea no registrada: impedirla y registrar decisión.

## Tareas

- [x] 1. Políticas: `work/policy.py`; modelos de decisión, reglas y adaptador Decisions. Tests de negativa, baja confianza, opción no elegible y error de proveedor.
- [x] 2. Runtime: `work/graphs.py`, `work/testing.py` y configuración de Agent Server separada. Supervisor existente + middleware async real, hijos Deep Agents, modo live/offline y trazas. Tests de límites y decisiones observables.
- [x] 3. Servicio: `work/contracts.py`, `work/store.py`, `work/service.py`, `work/engine.py`. Contratos versionados, comandos idempotentes, revisión, consulta y cancelación. Tests con backend controlado y servidor real.
- [x] 4. Interfaces: `work/api.py`, `work/mcp.py`, ejemplo ejecutable y configuración PyCharm. Mismo contrato por HTTP y MCP; pruebas de paridad y smoke local.
- [x] 5. Verificación y documentación: ejecutar suite, lint y smoke de lifecycle; documentar breakpoints, límites y resultado comprobado; actualizar estado de los documentos previos.

Cada tarea comienza con una prueba fallida del comportamiento nuevo y termina con sus pruebas específicas. La suite completa se ejecuta tras la integración. Se registra evidencia en `docs/progress/2026-10-22-async-supervisor-pilot.md`.
