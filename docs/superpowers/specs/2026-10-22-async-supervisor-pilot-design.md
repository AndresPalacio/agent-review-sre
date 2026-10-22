# Piloto de supervisor asíncrono para SRE Graph

Implementa la alternativa B del [tradeoff](../../arquitecture-review/2026-10-22-tradeoff-orquestacion-agentes.md), autorizada con «aplica las mejoras».

El usuario debe poder iniciar una investigación, ver las llamadas que el supervisor eligió, obtener identificadores de hijos que siguen trabajando, dirigirlos y cancelar trabajo. La misma capacidad será accesible por HTTP y MCP. La configuración actual del producto permanece disponible como baseline.

## Corte implementable

- Paquete optativo `sre_graph.work`: contratos y ledger SQLite de comandos, política de routing intercambiable, servicio de trabajos y adaptador al SDK de Agent Server. El servidor es el único motor de ejecución; SQLite conserva identidad y comandos del piloto.
- Supervisor construido con la fábrica existente y `AsyncSubAgentMiddleware` real. Dos hijos Deep Agents de lectura, métricas y logs, con notificación nativa. Los datos iniciales son fixtures sintéticos explícitos, sin acceso a infraestructura productiva.
- Modo `offline` con decisiones de prueba identificadas; modo `provider:model` para que un LLM real elija las herramientas. No presentar el modo offline como inferencia real. La falta de credenciales impide medir el proveedor, pero no probar ejecución, protocolos y recuperación local.
- Routing `direct`, `investigate`, `clarify`: reglas por defecto o adaptador OpenAI Decisions opcional. Registrar proveedor, selección y fallback. Nunca usar probabilidades para autorizar cambios.
- API local y MCP Streamable HTTP montado en el mismo gateway con `work_start`, `work_get`, `work_update`, `work_cancel`; mismo servicio y actor configurado fuera de los argumentos del modelo. Actualizaciones requieren revisión esperada e identificador de comando. Compartir proceso evita dos escritores del ledger.
- Configuración separada de Agent Server y comandos Run/Debug. El piloto no sustituye `langgraph.json` del RCA. La reanudación del servicio conserva la identidad y consulta el servidor; se verifica reinicio del proceso del gateway con motor vivo. La recuperación del motor productivo queda fuera del alcance de `langgraph dev`.

## Límites y consistencia

El piloto sirve a un actor local configurado, en un único proceso de gateway. No se expone como API pública multiusuario. El servidor de agentes se conecta por loopback por defecto. Los comandos se registran antes del envío y la metadata permite reconciliar una respuesta perdida; un envío incierto no se repite silenciosamente.

Inicio idempotente por actor y command_id; reutilizar un comando con otro payload es conflicto. Actualizaciones usan revisiones; un resultado viejo no reemplaza la revisión actual. Cancelar registra primero la intención y cancela padre e hijos conocidos, volviendo a consultar hijos para reconciliar. La solicitud no se presenta como cancelación efectiva hasta observarla.

El supervisor tiene presupuesto de llamadas y máximo de hijos. Las herramientas async se validan contra el contexto y estado del trabajo, con trazas redactadas. No se aceptan actualizaciones que descarten una aprobación pendiente. Las herramientas del piloto son de lectura.

## Aceptación

Pruebas de routing con SDK real y transporte HTTP controlado; contrato de comandos, propiedad, duplicados, revisiones y respuesta perdida; tests del middleware real con hijos; smoke con Agent Server real, HTTP y MCP, actualización durante trabajo y reconexión del servicio. Regresión de Python y lint correspondientes. Modo live preparado, sin afirmar ejecución de proveedores sin credenciales.

A2A, adopción de Temporal, infraestructura productiva y sustitución del motor RCA quedan aplazados conforme al tradeoff. El piloto aporta una base ejecutable para medir esas decisiones.
