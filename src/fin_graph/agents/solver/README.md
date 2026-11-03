# Solver asíncrono acotado

## Entrada

Ticker, horizonte y fixtures financieros sintéticos usados por el piloto del solver.

## Estado

`PilotContext` identifica la solicitud. El supervisor y sus hijos no acceden a archivos, shell ni
herramientas de escritura.

## Agentes

`supervisor` delega como máximo a `fundamentals` y `technicals`; ambos se construyen fuera del event
loop y notifican al padre mediante el middleware asíncrono.

## Herramientas

Cada hijo recibe exactamente una herramienta de fixture de solo lectura y límites de llamadas.

## Salida

Observaciones sintéticas etiquetadas como tales; el piloto nunca recomienda operaciones.

## Pruebas

`tests/agents/solver` comprueba delegación, control asíncrono, guardrails y ausencia de capacidades
no autorizadas.
