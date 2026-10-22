# better-harness Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans task by task.

**Goal:** Integrar optimización reproducible del RCA con better-harness y evals train/holdout.

**Architecture:** Reutilizar el grafo, fixtures y scores actuales. Consumir el
proposer y artefactos oficiales con un runner SRE y aceptación conservadora.

**Tech Stack:** Python 3.12, uv, pytest, LangGraph, Deep Agents, better-harness.

**Spec:** `docs/superpowers/specs/2026-10-21-better-harness-design.md`.

## Global Constraints

- Mantener Python 3.12 y los contratos de seguridad existentes.
- No usar credenciales ni integraciones en smoke; no llamar calidad a scripted.
- Preservar los artefactos locales preexistentes en `projects/`.
- Trabajar en el checkout solicitado, sin publicar, desplegar ni alterar secretos.

## Review Focus

- Familia duplicada entre splits: rechazar el manifiesto.
- Citas vacías o evidencia ausente: no conceder un aprobado RCA.
- Error o timeout de un caso: conservar fallo y continuar; no aceptar variante.
- Propuesta sin cambios, inválida o con escapes de rutas: no aplicar superficies.
- Run repetido o interrupción: no sobrescribir evidencia de otra ejecución.

## Tasks

### 1. Corpus y criterios de calidad
- [x] Escribir y ejecutar regresiones para splits, huellas, duplicados y criterios.
- [x] Implementar `sre_graph.harness.dataset` y `sre_graph.harness.quality`.
- [x] Versionar `harness/dataset.json`; verificar cobertura de los 36 casos.

### 2. Superficies y evaluación
- [x] Probar carga/restauración de prompts y selección de casos del runner.
- [x] Extraer prompts del runtime; ampliar `run_suite` con selección y timeout.
- [x] Implementar runner SRE con métricas por caso, repeticiones y artefactos.

### 3. Ciclo better-harness y CLI
- [x] Fijar upstream en extra `harness`; probar aceptación, bloqueo y privacidad.
- [x] Implementar configuración, proposer train-only, ciclo y CLI.
- [x] Añadir adaptador pytest y comprobar compatibilidad de configuración upstream.

### 4. Entrega y verificación
- [x] Documentar instalación, smoke, baseline, optimización y límites estadísticos.
- [x] Añadir CI offline y ejecutar smoke de todos los splits.
- [x] Ejecutar lint, format, mypy, import-linter y suite completa.
- [x] Revisar cambios y registrar resultados medidos, sin afirmar calidad no medida.
