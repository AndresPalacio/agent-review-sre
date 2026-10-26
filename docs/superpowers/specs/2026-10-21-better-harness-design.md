# better-harness para fin-graph

Implementar el ciclo completo solicitado sobre el grafo RCA existente. Deep Agents
actúa como optimizador externo usando el paquete oficial better-harness, fijado a
un commit. La migración del agente interno no forma parte de esta integración.

## Contrato

- Corpus versionado: los 36 escenarios existentes se asignan explícitamente a
  train, holdout y scorecard. Familias causales y duplicados conocidos permanecen
  juntos; una huella detecta cambios del corpus. Estratos: EKS y RDS.
- El optimizador recibe solo prompts editables, evidencia/referencias train y
  fallos train. Holdout se usa para aceptación; scorecard solo al inicio/final.
  Esto es separación de datos, no una frontera de seguridad frente a código hostil.
- Las únicas superficies editables son cuatro textos: planificación,
  investigación, síntesis y juez de claims. Herramientas, límites, evaluadores,
  permisos y controles de seguridad quedan fuera. Los textos los carga el runtime.
- Un caso exige ejecución completa, categoría correcta, keywords obligatorias,
  fuentes requeridas, consultas requeridas, citas válidas y ninguna categoría o
  keyword prohibida. Las repeticiones deben pasar todas. Latencia, tokens y
  trayectoria se guardan para diagnóstico; no se inventa coste monetario.
- Se acepta un candidato solo si mejora train y no pierde ningún caso que pasaba
  en holdout. Errores, bloqueos, resultados incompletos y smoke no son calidad.
- Los runs son inmutables, con variantes, métricas, huellas, decisiones y reportes.
  Cada ejecución y propuesta tienen deadline y límites de iteraciones/repeticiones.
- Smoke ejecuta el grafo y herramientas sobre fixtures, sin LLM ni proveedores.
  La calidad y la optimización real necesitan credenciales y modelos explícitos.
- CLI para validar, smoke, baseline y optimizar; configuración compatible con el
  runner pytest de upstream; CI offline con artefactos. No se despliega ni cambia
  automáticamente la configuración de producción.

## Integración

Usar las estructuras, workspace y proposer Deep Agents de better-harness. Un
runner SRE nativo reutiliza `run_suite` para evitar un proceso Python por caso.
El adaptador aplica una aceptación más estricta que la suma train+holdout upstream.
Un adaptador pytest permite también usar los comandos oficiales de upstream.

## Validación

Pruebas de separación/huellas, evaluadores adversarios, restauración de superficies,
timeouts, fallos parciales, ausencia de credenciales, aceptación/rechazo y flujo CLI.
Ejecutar los 36 escenarios offline, lint, tipos, contratos de imports y suite completa.
Documentar expresamente cualquier evaluación real no ejecutada.
