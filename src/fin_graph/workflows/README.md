# Workflows deterministas

`workflows` contiene máquinas de estado LangGraph, no catálogos de agentes:

- `analysis`: ingesta, evidencia, planificación, especialistas, síntesis y verificación;
- `orders`: preparar, aprobar, ejecutar, verificar y, si hace falta, revertir una orden paper;
- `market_watch`: observar mercado y escalar movimientos relevantes a análisis.

El código decide transiciones, límites e idempotencia. Los servicios o agentes inyectados resuelven
solo los pasos que requieren modelo. La construcción productiva vive en `fin_graph.runtime`.
