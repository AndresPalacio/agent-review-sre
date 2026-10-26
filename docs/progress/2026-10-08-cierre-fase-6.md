# Correcciones de congruencia antes de fase 7

Plan: [cierre de fase 6](../superpowers/plans/2026-10-08-cierre-fase-6.md).
Referencia: [diseño base](../specs/2026-10-07-fin-graph-design.md).

La revisión funcional de las fases 0–6 encontró once problemas pese a que las
792 pruebas existentes pasaban. El usuario pidió corregirlos antes de avanzar
a la UI. Se trabajó en `fix/cierre-fase-6`, desde `1561a78`, conservando `.idea/`
y el worktree previo. No hubo llamadas a vendors reales, push ni despliegue.

## Cambios y regresiones

| Hallazgo | Corrección | Comprobación |
|---|---|---|
| Botón antiguo aprueba otra acción | Persistir el ID del interrupt junto al mensaje remoto; compararlo bajo el lock del hilo | Escala → rollback: el botón de escala se rechaza y sólo el de rollback puede continuar; también tras decisión externa |
| Claim débil aprueba sin juez | Exigir juicio semántico positivo antes de considerar soportado un claim WEAK | CPU con sólo un log irrelevante se degrada a unknown |
| Masking sólo en supervisor | Aplicar middleware reversible en cada especialista del chat | La herramienta devuelve IP real; ninguna llamada del modelo la recibe cruda |
| Recuperación K8s prematura | Exigir generación observada además de contadores y template/token | Status saludable de generación anterior no verifica restart/undo |
| Slack descarta bots externos | Filtrar por la identidad autenticada del bot propio y permitir bot_message externo | Alerta de integración se investiga una vez; bot propio y duplicados se ignoran |
| RCA sin destinos | Propagar destinos API/schedule y routing por severidad de webhooks | Reporte llega al cliente HTTP simulado; destinos incluidos en payload externo no controlan la entrega |
| Logs Insights cachea Running | Esperar estado terminal dentro del timeout; estados pendientes agotados son errores reintentables | Running/Scheduled → Complete; sólo resultado terminal se cachea |
| Redacción elimina cursor AWS | Normalizar NextToken superior de APIs paginadas a pagination_cursor | El modelo usa el cursor visible en la segunda página; credenciales siguen redactadas |
| Evals consulta fechas actuales | Anclar reloj a tiempos grabados del escenario y conservar avance real del deadline | Ruta no scripted recibe métricas históricas usando la ventana entregada al modelo |
| CLI bloquea lotes HITL | Generar una decisión approve/reject por acción visible | Dos notas se aprueban o rechazan en local/remoto sin perder solicitudes posteriores |
| Ventana no crece con respuesta vacía | Distinguir observaciones de contenedores vacíos y metadatos | Refinamiento amplía el rango; muestras cero/false siguen siendo evidencia |

La entrega de RCA usa también el ledger durable existente, con clave por
ejecución/canal/destino. Reanudar la misma ejecución no repite envíos; una nueva
ejecución del mismo fingerprint puede entregar de nuevo. Un resultado remoto
incierto queda bloqueado para reconciliación manual.

## Decisiones de implementación

- El vínculo de aprobación usa la migración `0010_gateway_approval`, evitando
  alargar el callback de Telegram. Los mensajes antiguos sin ID de interrupt no
  autorizan acciones tras actualizar; sus solicitudes siguen accesibles por API/CLI.
- El campo visible de paginación AWS es `pagination_cursor`; las herramientas
  siguen aceptando `next_token`. No se introducen excepciones generales para
  secretos cuyo nombre termine en token.
- `/approve approve` y `/approve reject` aplican al lote mostrado. `/approve edit`
  conserva edición individual; rechaza lotes antes de auditar o ejecutar para
  evitar ediciones parcialmente aplicadas. No se añade una transacción distribuida
  entre herramientas o vendors.
- Las rutas de entrega de webhooks proceden de configuración del operador,
  nunca de canales o destinos suministrados por el payload de alerta.
- La evaluación scripted sigue siendo una prueba de funcionamiento, no una
  medición de calidad de un LLM real.

## Verificación

Gate global completado: **880 pruebas aprobadas, ninguna omitida**, con
`FIN_GRAPH_REQUIRE_POSTGRES=1` y Postgres local en `localhost:5433` (34,87 s).
Son 88 pruebas más que la base revisada. Ruff check y formato (313 archivos),
mypy (221 fuentes) e import-linter aprobaron. El smoke offline terminó con
**3 completadas, 0 fallidas y 0 bloqueadas**. `uv build --wheel --offline`
construyó el paquete y se verificó que incluye `0010_gateway_approval` y las
fixtures históricas de evaluación. `git diff --check` no detectó problemas.

Las revisiones independientes se aplicaron a gateways/AWS/chat/K8s y RCA/evals.
La primera ronda detectó formatos vacíos adicionales y una edición de lote que
podía aplicarse parcialmente; ambos casos se incorporaron a las regresiones de
cierre. La revisión posterior aprobó la corrección del CLI y los flujos de
entrega; el gate global incluyó todas las regresiones de formatos vacíos.
La validación con proveedores y modelos reales continúa pendiente de
credenciales de sandbox y no se sustituye por los tests offline.
