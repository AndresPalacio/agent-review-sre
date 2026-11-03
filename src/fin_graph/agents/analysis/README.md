# Equipo de análisis financiero

## Entrada

Una solicitud normalizada, contexto de ejecución, herramientas financieras de lectura y servicios
de snapshot de mercado.

## Estado

El supervisor conserva mensajes y delegaciones; el workflow de análisis conserva la evidencia,
tesis, decisiones y errores fuera del agente.

## Agentes

`make_persona_analysis_supervisor` coordina 19 personas creadas por `make_persona_agent`. Sus IDs y
prompts proceden del catálogo de dominio, sin duplicar listas.

## Herramientas

Cada persona recibe solo herramientas de lectura compatibles con su dominio. Middleware común
aplica límites, masking, caché y guardrails.

## Salida

Hallazgos y mensajes estructurados que el adaptador de referencia o un consumidor nativo puede
convertir en un reporte financiero.

## Pruebas

`tests/agents/analysis` cubre catálogo, prompts, delegación, límites y paridad de las 19 personas.
