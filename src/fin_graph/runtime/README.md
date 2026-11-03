# Runtime productivo

`analysis.py` ensambla servicios, integraciones y el workflow de análisis para CLI, API y LangGraph
Studio. `profile.py` fija presupuestos y huella de variante; `observation.py` y `trace.py` registran
eventos sin convertir el runtime en un evaluador.

Este paquete no importa `better_harness` ni `fin_graph.optimization`. La aplicación funciona con la
instalación base/app y solo carga el optimizador cuando se invoca explícitamente `fin-graph harness`.
