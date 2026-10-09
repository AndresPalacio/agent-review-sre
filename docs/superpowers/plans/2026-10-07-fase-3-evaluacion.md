# Fase 3 — arnés de evaluación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** evaluar el grafo RCA con los 36 escenarios históricos de OpenSRE, un backend offline reproducible, scorers y `sre-graph eval run`.

**Architecture:** Los datos portados viven dentro del paquete y se cargan con herencia de metadata y fallback por archivo de evidencia. `FixtureBackend` crea clientes falsos tipados y un `RunContext` para ejecutar las tools de lectura existentes sin red; el grafo recibe esas tools sin cambios. Un runner separa un smoke guionado de las corridas con modelo real y escribe resultados JSON/Markdown sin confundir smoke con calidad RCA.

**Tech Stack:** Python 3.12, LangGraph, LangChain, Pydantic, PyYAML, Typer, pytest.

**Spec:** `docs/specs/2026-10-07-sre-graph-design.md`, sección «Evaluación» y fila de Fase 3.

## Global Constraints

- Rama normal `fase-3` desde `main` integrado; sin worktree nuevo, push, despliegue ni consultas a servicios reales durante tests. Preservar `.idea/` y el worktree ajeno de 1C.
- Portar datos de `../opensre` commit `ce77b4a0b^`, suites `eks` (14), `rds_postgres` (21), `rds_upstream` (1); solo JSON/YAML necesarios. Atribución existente en `NOTICE`; no dependencia de ejecución a OpenSRE.
- Las llamadas de evaluación nunca llevan `destinations`; las tools son de solo lectura. El runner no modifica infraestructura ni expone credenciales.
- `smoke` es una prueba guionada del cableado y no cuenta como baseline de acierto; `eks`, `rds` y `all` con modelo real solo se consideran validadas al ejecutarlas con credenciales.
- Gate: pytest completo con Postgres local, Ruff, formato, mypy, import-linter y smoke CLI offline. Documentar pruebas bloqueadas.

## Decisiones y ajustes al diseño

- Los `scenario.yml` heredan metadata de `000-healthy` por mezcla superficial; una fixture ausente cae al archivo del escenario base. Las métricas CloudWatch divididas se ensamblan en orden estable.
- La tabla de mapeo conserva nombres originales de fuente y trayectoria junto al nombre de tool actual; fuentes sin tool equivalente se reportan como cobertura faltante, nunca como evidencia usada. `rds_upstream` requiere añadir lectores AWS EC2/ELB de solo lectura. Las métricas DNS/mesh usan las tools Prometheus con clientes falsos.
- Un modelo guionado puede usar respuestas predefinidas para el smoke; el reporte identifica el modo `scripted` y excluye sus scores de la línea base. Los runs reales usan el mismo grafo, backend y scorers, pero `LangChainServices` con el modelo elegido.
- El score de juez LLM es opcional y se distingue de los scorers deterministas. Tokens ausentes se declaran «no disponibles» en lugar de cero observado.

## Review Focus

1. Fixture heredada: el override del escenario gana y la base no se modifica; test `test_base_inheritance_and_split_cloudwatch`.
2. Escenario sin fuente declarada: el backend no sirve archivos ajenos ni toca red; test `test_fixture_backend_exposes_only_declared_sources`.
3. Cita inexistente o tool con error: el score de citas cae y no se cuenta fuente; test `test_invalid_citations_and_failed_evidence`.
4. Categoría prohibida aunque sea equivalente: fallo de categoría; test `test_forbidden_category_takes_priority`.
5. Falta de modelo/credenciales o fallo de una corrida: salida explícita `blocked`/`failed`, suite restante continúa y no se informa baseline aprobada; test `test_runner_records_blocked_run`.

## Mapa de archivos

| Ruta | Responsabilidad |
|---|---|
| `src/sre_graph/evals/data/` | 36 escenarios portados y manifiesto de procedencia |
| `src/sre_graph/evals/scenarios.py` | Modelos, loader y herencia segura |
| `src/sre_graph/evals/fixtures.py` | Clientes falsos, contexto y mapeo fuente↔tool |
| `src/sre_graph/evals/scoring.py` | Scorers deterministas y juez opcional |
| `src/sre_graph/evals/runner.py` | Selección de suite, repeticiones, ejecución y artefactos |
| `src/sre_graph/cli/main.py` | Subcomando `eval run` |
| `tests/evals/`, `.github/workflows/ci.yml`, `README.md`, `docs/progress/` | Cobertura, CI y evidencia |

### Task 1: dataset y loader

**Interfaces:** `Scenario` contiene suite/id, alerta, metadata, answer y `evidence: dict[str, Any]`; `load_scenario(suite: str, scenario_id: str) -> Scenario` y `load_suite(suite: str) -> list[Scenario]` validan rutas y contenido. `smoke` selecciona `eks/000-healthy`, `eks/001-oomkilled-crashloop` y `rds_postgres/001-replication-lag`; `eks` 14, `rds` 22, `all` 36.

- [ ] Escribir tests de conteo, `eks/001`, herencia, métricas divididas, rutas inválidas y campos de respuesta; correr RED.
- [ ] Portar solo datos de los 36 directorios desde el commit citado; añadir dependencia explícita PyYAML, loader y manifiesto de procedencia; correr GREEN.
- [ ] Verificar que wheel contiene datos, Ruff/mypy y tests; commit `feat(evals): port 36 scenarios and loader`.

### Task 2: FixtureBackend

**Interfaces:** `FixtureBackend(scenario: Scenario).registry` y `.context` entregan tools registradas y clientes falsos Kubernetes, AWS, Datadog y Prometheus según fuentes; `source_to_tool: dict[str, str]` es explícita y `coverage` expone faltantes. Cada lectura devuelve solo el payload del escenario o falla cerrado.

- [ ] Escribir tests RED de llamadas reales a tools EKS y RDS, aislamiento entre escenarios, ausencia de red, mapeo y fuentes faltantes.
- [ ] Implementar clientes falsos tipados y adaptaciones de payload; añadir lectores AWS EC2/ELB con pruebas offline; correr GREEN.
- [ ] Ruff/mypy/import-linter y tests; commit `feat(evals): add offline fixture backend`.

### Task 3: scorers

**Interfaces:** `score_scenario(scenario, state, trajectory, elapsed, usage) -> ScenarioScore` calcula categoría/equivalentes/prohibidas, keywords requeridas y de descarte, fuentes, citas, cobertura/extras/olas y latencia/tokens. `judge_against_reference(...)` es opcional y devuelve estado `not_run` sin modelo.

- [ ] Escribir tests RED para categoría prohibida, parcialidad de keywords, evidencia fallida, cita inválida, trayectoria y uso faltante.
- [ ] Implementar scorers puros, serialización JSON y Markdown compacto; correr GREEN.
- [ ] Ruff/mypy y tests; commit `feat(evals): score RCA quality and trajectory`.

### Task 4: runner, CLI, CI y cierre

**Interfaces:** `sre-graph eval run --suite smoke|eks|rds|all --model scripted|provider:model --repeat N --output-dir PATH`. El runner invoca el grafo con `FixtureBackend`, no entrega; escribe `results.json` y `report.md` con estado por corrida. Smoke usa servicio/modelo guionado solo para verificar el pipeline; real inicializa `LangChainServices` y mide uso/latencia.

- [ ] Escribir tests RED del CLI y runner: smoke offline con fixtures, artefactos, repeat, fallo aislado, modelo ausente y ausencia de envíos.
- [ ] Implementar runner/CLI, integración CI smoke y README; ejecutar smoke GREEN y documentar la línea base real bloqueada sin credenciales.
- [ ] Gate completo con Postgres, revisión independiente, correcciones y commit final. Mantener Fase 3 en rama local hasta una instrucción de integración.
