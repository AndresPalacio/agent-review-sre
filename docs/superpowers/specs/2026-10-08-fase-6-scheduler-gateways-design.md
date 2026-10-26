# Fase 6 — scheduler y gateways

## Objetivo y alcance

Completar la fase 6 del [diseño base](../../specs/2026-10-07-fin-graph-design.md):
chequeos programados y digest, retención y vencimiento de aprobaciones, entrada
Slack/Telegram y aprobación desde los bots. Las acciones mutantes continúan pasando
por el contrato HITL de fase 5. Se trabaja en la rama normal `fase-6`, sin push,
despliegue, cambios de infraestructura ni mensajes a servicios reales en las pruebas.

## Scheduler

El servidor Postgres usa APScheduler 3.x con `SQLAlchemyJobStore`. Una conexión
dedicada retiene un advisory lock durante la vida del scheduler; si no se adquiere,
el proceso sirve la API pero no ejecuta trabajos. Los jobs persistidos referencian
funciones de módulo y IDs de schedule, nunca objetos de aplicación, credenciales ni
payloads de incidentes. Al arrancar, el líder reconcilia las definiciones de la
tabla `schedules` con el job store. Los cambios de schedules actualizan la tabla y
el líder sincroniza el job de forma inmediata; otro proceso los detecta en su ciclo
de reconciliación. `max_instances=1`, coalescing y límites de misfire evitan ráfagas.

Las definiciones admiten `health_check` con intervalo en minutos y `digest` con
hora/minuto y zona IANA. Son explícitas y deshabilitadas por defecto. La API permite
listar, crear, actualizar, deshabilitar y disparar manualmente schedules; el CLI
remoto ofrece las mismas operaciones. Cada ejecución tiene una clave por schedule y
ventana para deduplicar una escalada tras reinicios. Se conserva un registro acotado
del último resultado, sin secretos ni transcript.

El grafo `health_check` ejecuta probes deterministas de solo lectura con parámetros
validados. La primera versión utiliza queries instantáneas de Prometheus y compara
sus valores con umbrales; errores o datos ausentes producen `unknown`, nunca
`healthy`. `assess` puede añadir una explicación mediante el modelo `fast`, pero la
decisión de umbral no depende de él. `escalate` marca una anomalía y el scheduler crea
una investigación en un hilo propio. `digest` resume de forma determinista los
resultados recientes y entrega el mensaje solo a destinos configurados.

Retención pasa al scheduler. El vencimiento localiza checkpoints de chat y
remediación con aprobación pendiente más antiguos que `APPROVAL_TTL_HOURS`, toma el
lock del hilo, vuelve a comprobar el estado y reanuda con `reject` como actor interno
`scheduler:expiry`. Ese actor únicamente puede rechazar; queda auditado. Una
aprobación ya resuelta o un hilo activo no se toca.

## Gateways

Slack usa Bolt `AsyncApp`: petición HTTP firmada en FastAPI para producción y Socket
Mode optativo en desarrollo. Telegram usa webhook con token secreto de cabecera.
Ambos verifican el origen antes de aceptar eventos, limitan tamaño, ignoran mensajes
del propio bot y deduplican IDs de evento/update. El ack se produce antes del
trabajo de investigación. El gateway acepta alertas solo de un canal/chat permitido,
publica progreso, guarda la correspondencia remota ↔ hilo en Postgres y actualiza
el mensaje con el reporte. Los reportes usan los renderers existentes y redacción.

Los botones contienen solo tipo de hilo, ID, decisión y un nonce/registro corto;
no contienen argumentos ni secretos. Al recibir un botón, el gateway comprueba
allowlist exacta de usuario para la plataforma, que el mensaje pertenece al hilo
registrado y que la aprobación sigue pendiente. El servicio compartido de aprobación
aplica el mismo esquema `decisions`, locks, revalidación y auditoría de la API. Los
botones permiten aprobar o rechazar; la edición se hace por API/CLI. Una pulsación
duplicada responde que la decisión ya se resolvió y no reejecuta la acción.

El evento autenticado se guarda antes del ack en un inbox Postgres con payload
normalizado y redactado. La investigación o decisión corre fuera de la petición;
un job del líder recupera los pendientes sin heartbeat tras dos minutos. Cada
alerta usa un hilo estable derivado del ID del proveedor. Las aprobaciones usan
un advisory lock Postgres por tipo e hilo, compartido entre procesos, además del
lock local. Una caída justo después del envío remoto puede dejar incierto el
estado de ese mensaje; la recuperación conserva la investigación y el ledger
impide repetir automáticamente una acción mutante.

## Errores, seguridad y pruebas

Credenciales y allowlists vienen de `SecretStr`/settings; nunca del payload ni del
checkpoint. Fallos de entrega se registran sin token ni contenido crudo. No hay
intentos automáticos de repetir mutaciones con resultado incierto. El scheduler no
ejecuta trabajos si no tiene el lock o falta la base de datos. Tests offline usan
tiempo congelado, ASGI, clientes HTTP simulados y Postgres local cuando existe.
El gate incluye pytest, Ruff, formato, mypy, import-linter y evaluación smoke.
La prueba manual en un workspace Slack/Telegram sandbox requiere credenciales y
queda documentada, sin simular que se ejecutó.

## Ajustes respecto al diseño base

El diseño menciona LLM `fast` después de umbrales. La explicación con modelo es
opcional para que health checks deterministas funcionen sin credenciales; no puede
cambiar la clasificación. La UI de schedules pertenece a fase 7. No se implementa
multi-réplica activa ni fan-out SSE; esa ampliación sigue en fase 8.
