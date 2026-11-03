# Agente optimizador opcional

## Entrada

Un prompt de optimización y un directorio de workspace preparado por better-harness.

## Estado

El historial y las variantes pertenecen a `fin_graph.optimization.harness`; esta fábrica solo crea
el agente que opera dentro del workspace virtual.

## Agentes

`make_optimizer_agent` crea un DeepAgent opcional. No se importa al arrancar el runtime productivo.

## Herramientas

Backend de archivos restringido a `workspace_root`, sin acceso deliberado a superficies ajenas al
manifiesto editable.

## Salida

Archivos candidatos y una explicación de la propuesta; nunca adopta ni despliega cambios por sí
solo.

## Pruebas

`tests/agents/optimizer` prueba la fábrica y `tests/optimization/harness` prueba el ciclo aislado.
