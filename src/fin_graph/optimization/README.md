# Optimización opcional

Este paquete integra better-harness y no participa en el arranque normal. `harness/` contiene el
dataset congelado, evaluación de variantes, proposer, worker aislado y exportación de artefactos.

La única fábrica DeepAgents del optimizador permanece en `fin_graph.agents.optimizer`; esta capa la
consume para proponer cambios dentro de un workspace acotado.
