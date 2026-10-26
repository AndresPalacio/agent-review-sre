# Validación de better-harness — 2026-10-21

Este documento registra la primera integración. La ampliación posterior, con nueve
superficies y contratos de tools/agentes, está en
[validación de better-harness completo](2026-10-21-better-harness-full.md).

Integración implementada sobre el RCA existente. El agente externo usa Deep Agents
y el paquete oficial better-harness fijado a
`be75459c444b53d21d633ef2ab997a01d0739c71`. El corpus queda en 19 train,
10 holdout y 7 scorecard; las familias y duplicados conocidos permanecen juntos.

## Evidencia ejecutada localmente

| Comprobación | Resultado |
|---|---|
| `uv run --extra harness pytest -q` | **1074 passed, 18 skipped**, 57,29 s |
| `uv run --extra harness pytest evals/harness --harness-model scripted -q` | **36 passed** |
| `fin-graph harness smoke` | **19/19 train, 10/10 holdout, 7/7 scorecard** |
| Ruff lint y format | Correctos; 383 archivos con formato verificado |
| mypy | Correcto; 248 módulos |
| import-linter | Contrato conservado |
| Validación SRE y upstream | Correctas |
| Inventario pytest upstream | Los 36 nodeids del manifiesto |
| `npm test` | **16 passed**, 5 archivos |
| `npm run typecheck` y `npm run build` | Correctos |
| `npm run test:e2e` | **5 passed**, Chromium |
| `uv build --wheel` | Correcto; contiene dataset del harness y UI compilada |
| `git diff --check` | Correcto |

Smoke queda guardado en `.harness-runs/final-20261021/`. El reporte confirma
`kind=wiring`, `quality_eligible=false` y `promotable=false`. Los 18 skips
corresponden a Postgres, porque `FIN_GRAPH_TEST_DATABASE_URL` no está configurada.
CI tiene un servicio Postgres y se añadió un job específico del harness para
Linux/Windows con artefactos; no se afirma haber ejecutado CI remoto.

La comprobación con proveedor en `.harness-runs/provider-check-20261021/`
terminó correctamente como **blocked**, exit code 1, por ausencia de
`OPENAI_API_KEY`. Tampoco hay `ANTHROPIC_API_KEY` local. No hubo evaluaciones
reales de calidad ni una mejora de modelo medida. El test del proposer sí invoca
el Deep Agent real y sus herramientas de archivos con un modelo scripted.

## Revisión y correcciones

La revisión independiente encontró que la política de referencias no confiables
había quedado dentro del prompt editable. Se trasladó a una constante fija del
servicio; la prueba de reemplazo del prompt falló antes de la corrección y pasó
después. También se cubrieron directamente las ramas de no-op, evaluación
incompleta, regresión scorecard y escape por symlink. No quedaron esos hallazgos
pendientes.

La suite completa reveló defectos previos al cambio, confirmados contra HEAD:
el evento de planificación se emitía con tipo vacío, la vista de explicación
exponía un resumen de evidencia excluido por su contrato, y la UI trataba una
verificación `null` como rechazo y una causa vacía como texto visible. Se
corrigieron con sus regresiones. Se conservaron los ids de hipótesis que ya
emitían el grafo/API y se alinearon las expectativas antiguas de esos tests.

## Decisiones y límites

- Se integró el optimizador sobre el grafo actual. Migrar el agente interno a
  Deep Agents sería un trabajo distinto; no se obtuvo una petición adicional.
- Se aceptan mejoras train sin perder aciertos holdout por caso, una regla más
  conservadora que upstream. Puede descartar mejoras globales con intercambios
  de aciertos; es una decisión deliberada.
- El manifiesto y scorecard pequeño permiten comparar estas campañas, no
  demostrar generalización estadística. Se necesitan casos nuevos y modelos
  reales antes de decidir una adopción productiva.
- Se conservaron los artefactos locales previos de `projects/`; no forman parte
  de esta implementación. Ruff excluye `.scratch/` y `.runs/` generados.

Comandos y procedimiento de adopción: [guía completa](../better-harness.md).
