# Validación de la ampliación de better-harness

Implementación en `feat/better-harness-full`, sin commit, publicación ni despliegue.
Los cambios previos del workspace y `projects/` se conservaron. El plan es
[better-harness-full](../superpowers/plans/2026-10-21-better-harness-full.md).

## Continuación completada

- [x] Hacer que todos los puntos de entrada de investigación se ejecuten mediante el harness como flujo principal
  y predeterminado. Alcance y criterios de aceptación en la tarea 7 del
  [plan](../superpowers/plans/2026-10-21-better-harness-full.md#continuación-solicitada-por-el-usuario).
  Transición completada y verificada el 2026-10-22; evidencia al final de este documento.
- [x] Revisión final de `docs/arquitecture-review/` con referencia a Rovo (tarea 8),
  completada el 2026-10-22 después del cierre de la tarea 7.
  [Informe, prioridades y criterios de aceptación](../arquitecture-review/2026-10-22-revision-rovo.md).

## Resultado implementado

- Nueve superficies consumidas por producción: cuatro prompts, selección/descripciones
  de tools, skills, política de planificación, middleware y registro.
- Trazas reales de invocación con parentesco de especialista, argumentos redactados,
  orden y resultado, incluyendo concurrencia y cancelación.
- Contratos para los 36 casos; llamadas obligatorias/autorizadas, propietario,
  argumentos, ventanas de delegación, precedencias y presupuestos.
- Copias temporales del código y subprocess por split, con terminación por deadline.
- Presupuestos de latencia/tokens, juez externo opcional, feedback sólo train,
  protección emparejada de holdout y scorecard inicial/final.
- Inspección de contratos y resultados; exportación de archivos y diff aplicable,
  con fuente original capturada y hashes de los bytes exportados.

## Verificación final

| Comprobación | Resultado |
|---|---|
| `uv run --extra harness pytest -q` | **1108 passed, 18 skipped**, 72.68 s |
| `pytest evals/harness --harness-model scripted -q` | **36 passed**, 5.31 s |
| Smoke `.harness-runs/full-final-20261021` | Completed, 19/19 train, 10/10 holdout, 7/7 scorecard |
| SRE y upstream `validate` | Configuración válida, nueve superficies |
| Ruff check y format | Correctos; 405 archivos formateados |
| Mypy | Sin errores, 259 módulos |
| Import-linter | Contrato arquitectónico conservado |
| Exportación CLI | Correcta; manifiesto, archivos y diff revisables |
| `git apply --check` | Correcto sobre la fuente capturada, con cambios de prompts/skills |
| Wheel | Construido; incluye skills, contratos, dataset, worker y UI |
| Preflight real `.harness-runs/full-provider-check-20261021` | Blocked: `OPENAI_API_KEY` no configurada |

Las 18 omisiones requieren `FIN_GRAPH_TEST_DATABASE_URL` (Postgres). Las validaciones
scripted y las pruebas con modelos simulados prueban el software, **no calidad RCA ni
mejora de un modelo real**. Smoke mantiene `quality_eligible=false` y `promotable=false`.
No se ejecutó CI remoto ni se repitieron pruebas web: esta ampliación no cambia la UI.

## Revisión independiente y correcciones

La revisión de código detectó dos problemas reproducibles. Se agregaron pruebas que
fallaban y luego pasaron:

1. El diff de prompts usaba una fuente reconstruida, incompatible con el checkout.
   Ahora se captura la fuente original; se preservan formato y finales de línea en
   superficies sin cambios y se reemplazan únicamente asignaciones modificadas.
   Se comprobaron aplicación del diff y hashes de archivos exportados en Windows.
2. El contrato fijaba la ventana inicial, rechazando ampliaciones legítimas del grafo.
   Ahora cada delegación registra su ventana efectiva y el grader contrasta con ella.

También se aceptan especialistas cloud o metrics para RDS conforme al contrato de
tools. Las funciones/adaptadores, schemas, guardrails y evaluadores no son superficies
editables. El aislamiento por copia/proceso no es una frontera de seguridad contra
Python hostil; la guía describe esa limitación y la separación de datos de investigación.

Guía de uso y límites: [docs/better-harness.md](../better-harness.md).

## Cierre de la tarea 7: 2026-10-22

`harness.runtime` es la fábrica común para las investigaciones de CLI, chat, API/UI,
webhooks, gateways, escalamiento programado, Studio, replays y evals. Las fábricas
anteriores delegan a ella. `harness.profile` resuelve límites y huella de variante;
el runtime base no necesita las dependencias opcionales de optimización.

Se conservan el grafo compilado, checkpoints, streaming, cancelación, memoria y
controles de efectos externos. Las trazas persisten en checkpoints; los eventos
parciales del servidor sólo exponen metadatos. Se mantienen los límites existentes
de incidentes; los contratos por escenario y evaluación de tokens siguen en evals.

La revisión independiente final detectó una carencia de cronología entre nodos:
se corrigió con `observed_at`, un `clock_id` por proceso y `monotonic_ns` de alta
resolución. La prueba intercala dos especialistas y comprueba su orden y conservación
en checkpoint y evaluador. Se observó fallar antes de corregirla y pasar después.
Los contadores `seq`/`elapsed` de producción siguen siendo locales a cada nodo;
los ticks sólo se comparan dentro del mismo proceso. No hubo otros hallazgos materiales.

Las correcciones de la sesión interrumpida también quedaron verificadas: los workers
conservan los presupuestos de `.env` y pueden evaluar candidatos distintos de la variante
fijada para producción. El pin sigue siendo obligatorio en las entradas del producto.

| Comprobación | Resultado final local |
|---|---|
| `uv run --extra harness pytest -q` | **1124 passed, 18 skipped**, 93.81 s |
| `pytest evals/harness --harness-model scripted -q` | **36 passed** |
| Smoke después de corregir trazas | **19/19 train, 10/10 holdout, 7/7 scorecard** |
| Ruff check / format | Correctos, 412 archivos |
| Mypy | Correcto, 263 módulos |
| Import-linter | Contrato conservado |
| Web unitarias / Chromium E2E | **16 / 5 passed** |
| Web typecheck / build | Correctos |
| Wheel | Construido; runtime, worker, dataset, skills y UI comprobados |
| CLI runtime / validadores SRE y upstream / export | Correctos |

Artefactos: `.harness-runs/runtime-timestamp-final-20261022/` (smoke final),
`.harness-runs/runtime-final-20261022/` y `.harness-runs/runtime-review-20261022/`
(smoke y exportación previos a añadir las marcas temporales).

Una ejecución anterior de la suite falló por `database is locked` en
`tests/server/test_runtime_bounds.py::test_http_overload_precedes_webhook_claim`,
durante `RunManager.stop()`. La prueba aislada confirmó intermitencia y reprodujo
el mismo error usando el manager de `HEAD` en un proceso separado, sin modificar
el checkout (intento 5). El caso usa un grafo simulado sin eventos del harness y
la ruta de cancelación no cambia en este trabajo. Se registra como defecto previo (resuelto el 2026-10-22; ver al final)
pendiente de diagnóstico; la suite completa final pasó, pero no acredita resolverlo.

Los 18 skips requieren Postgres. No se ejecutó CI remoto ni se midió calidad con
proveedores reales. Scripted sólo valida el cableado y mantiene
`quality_eligible=false`, `promotable=false`. Los cambios permanecen sin commit,
publicación ni despliegue, en el checkout existente `feat/better-harness-full`.
La variante se adopta instalando código revisado y reiniciando; no hay carga dinámica
de Python candidato en el servidor. Esta decisión exige reinicio al cambiar variante.

## Revisión 2026-10-22

- `_ObservedServices` solo envuelve los métodos de investigación. Los demás
  atributos del servicio se devuelven sin modificar; antes se convertían en
  corrutinas.
- Reanudar un checkpoint con otro perfil sigue fallando de forma cerrada, pero el
  error ahora nombra la variante guardada, la activa y los campos que difieren.
- Verificación: `pytest -q` **1252 passed, 18 skipped**.

## Defecto `database is locked`: causa raíz y corrección (2026-10-22)

Medido sin cambios: falló 9 de 80 ejecuciones aisladas de
`test_http_overload_precedes_webhook_claim`. Cada fallo esperaba el *busy timeout*,
así que otra conexión retenía el bloqueo.

Causa, confirmada con instrumentación y reproducción aislada:

1. `RunManager.stop()` cancela la tarea del run mientras espera una consulta
   (`get_run`).
2. SQLAlchemy invalida y cierra la conexión, pero el cursor del `SELECT` cortado
   sigue referenciado por el traceback del `CancelledError`.
3. Con un statement sin finalizar, `sqlite3_close_v2` deja el handle *zombie* y
   conserva su bloqueo SHARED.
4. Las escrituras posteriores fallan: primero la del handler de cancelación de
   `_execute`, que corre con ese traceback vivo, y después la de `stop()`.

Sólo afecta a SQLite.

Corrección en `server/storage.py`: `_complete_on_cancel` envuelve cada operación
corrutina de `Storage`. La operación corre en su propia tarea, termina, y después
se relanza la cancelación del llamador. Así una cancelación nunca corta una
consulta a medias. Los context managers de locks siguen cancelables de inmediato.

Prueba determinista nueva en `tests/server/test_storage.py`: un progress handler
de SQLite cancela la tarea dentro del hilo de aiosqlite mientras el `SELECT` se
ejecuta, y la escritura se hace en el handler de cancelación, como en el manager.
Falló 3/3 antes y pasa después.

Verificación: el test original pasó 0 fallos en 80 ejecuciones (antes 9/80). La
suite completa, tres veces: **1256 passed, 18 skipped**. Ruff, MyPy (273 módulos)
e import-linter correctos.

