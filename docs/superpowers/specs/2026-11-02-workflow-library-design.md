# Biblioteca de workflows

Implementación autorizada por la petición de implementar/mejorar `como-funciona-workflow.md`.
La referencia describe otra aplicación y sus integraciones privadas, no fuentes disponibles
en este repositorio. Se implementa su contrato reutilizable, no una réplica binaria.

## Decisiones

Paquete independiente `packages/workflow` / `bm_workflow`, sin imports de `fin_graph`.
Alternativas consideradas: integrar el SDK en fin_graph (arrastra dependencias de producto),
copiar los 94 módulos corporativos (no hay fuentes ni servicios disponibles), o un paquete
pequeño con puertos inyectables (elegido por independencia y verificabilidad).

Núcleo: Python 3.12+, Pydantic 2 y LangGraph 1; LangSmith explícito para desactivar
tracing en simulación, ya transitivo de LangGraph. API declarativa con pasos raw/tipados,
rutas, contratos, digest canónico y registro por versión explícita. Ejecución async y
fachada sync; entrada/salida revalidadas, copias de estado, errores sin mensajes de proveedor,
cancelación/deadline y retry solo por error transitorio explícito y operación segura.

Composición: secuencia, rutas terminales, paralelo, join, map ordenado con workers
acotados y subflows. Adaptadores opcionales reciben modelos/tools/agentes del consumidor.
Dry run usa el grafo real con fixtures y bloquea fronteras externas no simuladas.

Runs: admisión separada, ownership mediante CAS, memoria y SQLite durable, separación
por namespace, pin de digest/revisión y estados terminales. Efectos con ledger que no
reenvía resultados inciertos. Integraciones privadas y autenticación HTTP corresponden
al host. No se promete recuperación por nodo ni exactly-once.

## Validación

Pruebas offline de contratos, aislamiento, concurrencia, versionado, errores, timeout,
simulación, CAS y reinicio SQLite. Comprobación de wheel e import sin extras, lint y tipos.
Suite existente para detectar regresiones; servicios externos no configurados se reportan.
