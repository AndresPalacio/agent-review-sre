# Fiabilidad y evaluación del solver

Continuación autorizada por el usuario el 3 de noviembre de 2026: investigar fuentes
oficiales, evaluar problemas completos, implementar mejoras justificadas y verificar
el resultado de forma autónoma. Conserva el paquete 0.2.0 ya implementado.

## Evidencia inicial

- Suite existente: 153 pruebas aprobadas en Python 3.14.
- Evaluación existente: 11 casos por brazo, solo tres familias y dos variantes
  reservadas. Es una prueba de contratos con modelos guionizados.
- Una respuesta que combina una acción y una solución estructurada puede terminar
  antes de ejecutar la herramienta pendiente.
- Las instancias de ProblemSpec construidas sin validación pueden saltarse criterios;
  el verificador puede mutar la propuesta que posteriormente se devuelve.
- El Runnable de resumen personalizado evita el prompt y la recuperación nativos
  ante ContextOverflowError. Una reproducción local falla donde el SDK se recupera.
- Los tokens parciales se presentan como totales; la autocorrección de una escritura
  incorrecta no cuenta como recuperación. La tasa de verificación del baseline es
  siempre cero por definición, aunque el evaluador independiente apruebe el entorno.

## Decisiones de implementación

Se mantiene el motor nativo. Las alternativas son reemplazar más del bucle del agente,
añadir otros agentes o memoria, o corregir las fronteras existentes y reutilizar el SDK.
Se elige la última por evidencia directa y menor superficie de mantenimiento. No se
actualizan dependencias: la documentación y el código inspeccionados son compatibles
con DeepAgents 0.7.32.

1. Permitir que LangChain ejecute las acciones pendientes antes de verificar la
   propuesta; conservar reparación acotada, cuotas, comprobación final y ausencia
   de efectos duplicados. Revalidar y aislar los datos del problema, límites y
   propuesta en las fronteras públicas; conservar identidad de clientes/contexto.
2. Consultar al verificador ante una terminación sin propuesta para reconocer un
   bloqueo del host. Ninguna aprobación sin propuesta puede producir éxito.
   Los errores arbitrarios de herramientas siguen siendo terminales: solo errores
   que el host declara manejables mediante mecanismos nativos permiten recuperación.
3. Encapsular el modelo de resumen con cuotas y cancelación; delegar el formato,
   referencias de medios y recuperación de contexto al middleware nativo.
   Cada intento de resumen, incluido el de recuperación, consume cuota.
   El especialista general predeterminado debe heredar las skills del padre como
   el predeterminado nativo; los especialistas personalizados mantienen fuentes explícitas.
4. Añadir investigación con múltiples fuentes obtenidas por herramientas y una
   variante reservada. Separar métricas por partición, verificación independiente
   y aprobación interna; medir recuperación observada y tokens completos/parciales.
   Rechazar reutilización accidental del modelo entre brazos.

## Límites

Sin servicios pagados en esta sesión: no hay credenciales de proveedor en el entorno
ni un presupuesto de evaluación autorizado. Las mejoras se acreditan como contratos
de ejecución, no como superioridad de razonamiento. Los casos reservados locales son
variantes públicas del repositorio, no una evaluación ciega de generalización.

Se mantienen Python 3.12/3.14, importación sin extras, independencia de fin_graph,
interceptores de dry-run, ausencia de reintentos automáticos de efectos inciertos y
cancelación cooperativa. No se publica, despliega, cambia la rama ni descarta trabajo.
