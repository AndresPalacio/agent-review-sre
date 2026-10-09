# Cierre técnico y fase 8

La petición es completar lo necesario después de fase 7. El diseño base define
v1 como fases 0–7 y fase 8 como trabajo continuo; este cierre toma todos sus
entregables concretos y las trece integraciones nombradas (Elastic/OpenSearch
se registran por separado). No convierte los puntos suspensivos en un catálogo
infinito ni reincorpora los extras de agentes de código excluidos de v1.

## Arquitectura

Postgres sigue siendo la autoridad durable. Los eventos se guardan antes de una
notificación transaccional que sólo contiene el ID del hilo. Cada proceso tiene
un listener LISTEN/NOTIFY y despierta sus streams mediante señales coalescentes;
los consumidores siempre recuperan del ledger mediante Last-Event-ID. Una caída
del listener no pierde eventos: reconecta y conserva un sondeo lento de respaldo.

El scheduler tiene un supervisor de elección independiente de la vida del líder.
Sólo la sesión que conserva el advisory lock despacha jobs; una réplica en espera
puede promoverse automáticamente. La reanudación de investigación se serializa
entre réplicas antes de crear una nueva corrida. Se acotan solicitudes activas,
streams y corridas pendientes; la saturación devuelve un error reintentable sin
ejecutar ni dejar corridas falsamente aceptadas.

La CLI añade list/verify/setup de integraciones, exportación/replay de incidentes
y comparación de evaluaciones. Setup valida antes de reemplazar configuración,
no imprime secretos y no acepta credenciales literales en argumentos. Los
escenarios grabados son distintos del dataset atribuido: contienen evidencia
real saneada y una respuesta de referencia revisada explícitamente por el operador.
Replay no tiene fallback a proveedores.

Los trece conectores nuevos ofrecen lectura operativa con endpoints fijos,
argumentos tipados, límites de respuesta/tiempo/ventana y verificación de lectura.
No se expone una herramienta HTTP genérica ni comandos de shell arbitrarios.
Las mutaciones continúan limitadas al ActionCatalog con preparación/aprobación.
MCP se conecta al arranque y a la CLI; sólo tools explícitamente declaradas como
lectura entran en los especialistas, y las demás conservan su clasificación
restrictiva. No se inventan servidores MCP remotos ni paquetes a ejecutar.

## Superficie finita de integraciones

| ID | Operaciones |
|---|---|
| opsgenie | Alertas, detalle, historial |
| incident_io | Incidentes, detalle, actualizaciones |
| jira | Búsqueda JQL nueva y detalle de issue |
| honeycomb | Consulta analítica acotada con polling de resultado |
| elasticsearch / opensearch | Logs con ventana obligatoria y salud |
| splunk | Búsqueda de logs construida a partir de términos; sin SPL arbitrario |
| victoria_logs | Consulta LogsQL acotada |
| coralogix | Consulta de logs DataPrime acotada |
| azure | Consulta Log Analytics en workspace configurado |
| argocd | Aplicaciones, resumen y diferencias; sin sync/refresh |
| helm | List/status/history/values/manifest; argv permitido, límites y saneamiento |
| gitlab | Commits, merge requests, pipelines y archivos de repositorio |

## Aceptación

Pruebas reales de Postgres para notificaciones, replay, reanudación concurrente y
promoción del scheduler. Carga offline con concurrencia y resultados medidos;
controles de tamaño/cabeceras/autorización/cancelación. Mocks por integración,
setup sin pérdida de configuración ni secretos, grabación→replay sin red,
comparación de evals, 36 escenarios guionados y regresión completa Python/web.
Construcción y comprobación del paquete instalado, Docker y UI.

La comprobación contra modelos/vendors reales requiere endpoints, credenciales
y destinos sandbox suministrados por el operador. Su ausencia no se simula como
éxito. No hay remoto Git configurado; se puede cerrar e integrar localmente, pero
no publicar a un destino inexistente. Se preparará una guía ejecutable de
aceptación real y un informe de prerrequisitos verificables.
