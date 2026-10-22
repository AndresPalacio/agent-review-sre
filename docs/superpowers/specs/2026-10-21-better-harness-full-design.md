# Better-harness completo para SRE

El usuario pide aprovechar better-harness manteniendo la robustez del sistema actual,
incluidas evaluaciones de tools y especialistas. Esta ampliación arquitectónica continúa
la implementación autorizada. Conserva LangGraph como agente objetivo y utiliza el
Deep Agent de upstream como optimizador; no cambia el producto por un agente de demo.

## Diseño

- Registrar invocaciones reales de servicios y callbacks de tools: identidad, argumentos
  redactados, parentesco, orden, duración y resultado. Persistir también trazas incompletas.
- Contratos de trayectoria inmutables por escenario: agentes y tools requeridos,
  argumentos, tools autorizadas, propietario de la llamada, precedencias y presupuestos.
  Evaluar junto con las condiciones existentes de diagnóstico y citas en cada repetición.
- Exponer prompts y archivos reales consumidos por producción: selección/descripción de
  tools, skills de investigación, política de planificación, middleware y su registro.
  Mantener fuera del conjunto editable guardrails, redacción, schemas, evaluadores y corpus.
- Evaluar archivos candidatos en copias temporales de src en procesos separados, con
  timeout y resultados cerrados ante error. No reemplazar archivos del checkout activo.
  Esto es aislamiento de ejecución, no una frontera de seguridad para Python hostil.
- Mantener train/holdout/scorecard y aceptación emparejada. Añadir límites medidos de
  latencia/tokens, feedback de trayectoria visible sólo para train, huellas de contratos,
  informe legible y exportación revisable del candidato. Nunca promoción automática.
- Compatibilidad con configuración, variantes, proposer y runner pytest de upstream.
  Harbor es un runner alternativo para otros benchmarks, no una dependencia de este RCA.

## Continuación: runtime predeterminado

La tarea 7 convierte `harness.runtime` en la fábrica común del producto y las evals.
El grafo compilado conserva su API; una envoltura por nodo añade trazas a checkpoints
y streaming usando scopes de contexto aislados entre incidentes y especialistas.
`harness.profile` resuelve límites y la identidad de las superficies instaladas, con
pin opcional. No se cargan candidatos Python dinámicamente en el servidor; la adopción
sigue siendo revisión, instalación y reinicio. Las dependencias del optimizador
siguen siendo opcionales. Los imports antiguos delegan al runtime común.

La compatibilidad conserva checkpoints antiguos sin perfil; los nuevos exigen el
perfil original al reanudar. El historial público sólo muestra metadatos de ejecución;
los argumentos redactados permanecen en los checkpoints y las trazas privadas de eval.
Las pruebas cubren entradas reales de API/webhook/scheduler y CLI, concurrencia,
cancelación y reanudación, además de la regresión completa y los tres splits.

## Verificación de la ampliación original

Casos positivos y negativos de cada contrato; concurrencia y fallos en trazas; consumo
real de todas las superficies; proceso que falla o agota timeout; aislamiento de cambios;
smoke de los 36 escenarios; runner oficial; regresión Python completa, lint/tipos/imports.
Las pruebas scripted no acreditan calidad de un LLM. Sin credenciales, documentar ese
límite y dejar las ejecuciones reales listas para ejecutarse, sin inventar resultados.
