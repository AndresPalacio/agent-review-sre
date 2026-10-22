# Progreso del piloto asíncrono

Plan: [implementación](../superpowers/plans/2026-10-22-async-supervisor-pilot.md).

- Diseño: alternativa B autorizada; se implementa piloto optativo, sin reemplazar producción.
- Entorno: claves OpenAI, Anthropic y LangSmith ausentes; no se afirmará validación live.
- Ruling: continuar sobre `feat/better-harness-full` y preservar todos los cambios previos; un checkout de HEAD omitiría el harness aún sin commit.
- Ruling: Agent Server es el motor único. El ledger del gateway registra comandos y revisiones, no ejecuta agentes ni implementa un scheduler paralelo.
- Estado: piloto implementado y verificado localmente; inferencia live pendiente de credenciales.

## Entrega

[Guía ejecutable, panel y breakpoints](../arquitecture-review/2026-10-22-piloto-async-ejecutable.md).

- Paquete `sre_graph.work`: política, contratos, ledger, servicio, adaptador Agent Server, supervisor e hijos, HTTP y MCP.
- Extra `agents` y lock actualizado; conserva los cambios de dependencias del harness que ya existían.
- `examples/demo_supervisor_async.py`: panel y escenarios lifecycle/cancel; `examples/demo_mcp_async.py`: cliente MCP real.
- `examples/async_pilot.html`: estado real, traza de herramientas, actualización, cancelación y reconexión.
- Tres configuraciones PyCharm: panel, Agent Server y cliente debug.
- Revisión Rovo y README enlazan el nuevo piloto. A2A/Jira y Temporal quedan diferidos según el tradeoff.

## Evidencia de verificación final

Entorno: Windows, Python 3.12; Deep Agents 0.7.29, LangGraph SDK 0.4.6, OpenAI SDK 3.26.0, MCP 1.30.0 y Agent Server 0.13.3. No se usaron claves de proveedores.

| Comprobación | Resultado observado |
|---|---|
| `python -m pytest tests/work -q` | **122 passed**, 4.69 s |
| `python -m pytest -q` | **1247 passed, 18 skipped**, 90.07 s; omisiones por `SRE_GRAPH_TEST_DATABASE_URL` ausente |
| Ruff check y format de paquete, tests y dos ejemplos nuevos | Sin errores; 17 archivos formateados |
| MyPy de `src/sre_graph/work` y ambos ejemplos | Sin errores, 12 archivos |
| `uv lock --check` | 166 paquetes resueltos; lock coherente |
| Agent Server real, lifecycle | Dos hijos, actualización de logs a `5m`, mismos thread IDs, reconexión de servicio y resultado de revisión 2 |
| Recuperación de notificación/contexto antiguo | El lifecycle final ejecutó una consolidación registrada en ledger y terminó `completed`, revisión 2 |
| MCP real por HTTP | `initialize`, `tools/list`, cuatro herramientas; repetir inicio por HTTP conserva trabajo, update observado, cancelación confirmada |
| Navegador Chromium | Inicio, reintento idempotente, actualización, resultado, recarga/reconexión y cancelación; sin errores JS; sin overflow a 390 px |
| Reinicio real del proceso del gateway | PID nuevo, mismo ledger, mismo work_id y mismos hijos mientras Agent Server sigue vivo; actualización posterior y resultado `5m`, revisión 2 |

Las capturas de navegador y scripts auxiliares de comprobación permanecen en `i/`, fuera de Git. Los ejemplos y pruebas reproducibles están en `examples/` y `tests/work/`.

## Fallos encontrados y corregidos

Las pruebas se escribieron antes de la implementación y reprodujeron las regresiones antes de aplicar cada corrección. La revisión independiente adicional encontró problemas de consistencia que se incorporaron a los tests:

1. Factories de hijos ejecutadas en el event loop activaban `BlockingError` de Deep Agents en Windows. Las factories async construyen agentes mediante `asyncio.to_thread`; no se deshabilitó el detector de bloqueo.
2. Usar solo el máximo `work_revision` permitía aceptar un resultado de contexto viejo. Ahora cada respuesta lleva `result_revision` y hay consolidación sin herramientas, idempotente por mensaje observado y acotada a tres intentos por revisión.
3. Una notificación tardía podía reabrir un trabajo terminado. `completed` es estable y conserva el resultado observado.
4. Una notificación perdida dejaba padre e hijo idle con estado cacheado running. El adaptador distingue `awaiting_parent` y solicita al mismo supervisor recuperar las notificaciones mediante su middleware. Un hijo huérfano terminado se señala como `recovery_required`.
5. Reconciliar un comando antiguo sobrescribía el run más reciente. El ledger identifica el último comando y preserva su ejecución.
6. Fallar al preparar un thread impedía recuperar el inicio, aunque no hubiese run. La preparación idempotente precede al registro de intención de ejecución; solo un envío de run incierto requiere reconciliación.
7. SQLite no cerraba conexiones al terminar la transacción. Se añadió cierre explícito y una regresión de handles en Windows.
8. Cancelar una respuesta local de aclaración quedaba pendiente sin thread remoto. Los trabajos terminados rechazan ese comando como conflicto.

## Límites que permanecen

No se validaron llamadas live a Decisions ni a modelos de chat; los SDK usan transporte controlado en las pruebas. El umbral de confianza 0.7 no está calibrado y no hay benchmark de calidad/coste frente a Rovo.

Este gateway es local, de un actor y un proceso. Se verificó el reinicio del gateway, no tolerancia a caída del motor productivo, múltiples réplicas ni la suite Postgres. Agent Server de desarrollo notificó que 0.13.3 está fuera de soporte; una actualización del runtime requiere su propia comprobación de compatibilidad antes de producción.

La consulta de aprobación pendiente y el `interrupt` posterior de Deep Agents no son una operación atómica remota. El piloto no ejecuta remediaciones ni ofrece una garantía distribuida de aprobación. Se conserva el runtime de producción previo y no se hicieron commits, push ni despliegues externos.

## Revisión 2026-10-22

Una revisión posterior de `sre_graph.work` reprodujo dos defectos con un script
externo. Se añadieron pruebas que primero fallaron y después pasaron:

1. Un `submit` rechazado antes de que el servidor aceptara el run dejaba el trabajo
   en `dispatching`. `GET` lo presentaba como `running` sin `run_id`. Ahora
   `_refresh` reconcilia el último comando con `find_run`. Si el motor no registra
   el run, el estado es `dispatch_uncertain` con `detail`; `update` lo rechaza y
   `cancel` lo cierra. No hay reenvío automático. Si el run aparece después, se
   reconcilia a `running`.
2. Al reconciliar una consolidación, un fallo de `find_run` se propagaba como
   error crudo (500). Ahora un helper único `_reconcile` lo convierte en
   `WorkUnavailable` (503).

Mejora: `start` ya no mantiene el lock del gateway mientras la política decide
(OpenAI Decisions puede tardar hasta 10 s). Se vuelve a comprobar la identidad
del comando antes de crear el trabajo; dos reintentos concurrentes hacen un único
envío. El panel muestra `detail` cuando existe.

Revisado sin cambios: `cancel_tree` se repite en cada lectura de un trabajo
cancelado porque una notificación tardía puede despertar al padre.

Implementado después de la revisión:

- `PilotGuard` falla cerrado si el contexto no trae `ledger_path`
  (`control_unavailable`). Antes omitía el control de actor, cancelación y revisión.
  Los tests de grafos usan un ledger por test que refleja la revisión despachada,
  como hace el gateway.
- Una cancelación confirmada marca `cancelled` a los hijos activos en la vista.
  Antes el trabajo aparecía `cancelled` con hijos `running`, un estado cacheado
  previo a la cancelación.

Verificación: `pytest -q` **1255 passed, 18 skipped**; Ruff, MyPy (`work`, `harness`)
e import-linter correctos. Agent Server real (offline, puerto 8037): `lifecycle`
completó la revisión 2 con ambos hijos; `cancel` terminó `cancelled` con ambos hijos `cancelled`.
