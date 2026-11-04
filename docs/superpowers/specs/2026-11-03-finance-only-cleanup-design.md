# Diseño: repositorio exclusivamente financiero

**Fecha:** 2026-11-03  
**Estado:** pendiente de aprobación

## Intención

Fin Graph debe ser un producto de análisis financiero multiagente, paper trading,
backtesting y evaluación financiera. El árbol de trabajo actual todavía contiene datos,
artefactos, ejemplos, nombres y compatibilidad del producto SRE que precedió a Fin Graph.
Aunque los clientes activos de AWS y Kubernetes ya se retiraron, algunos residuos todavía
se distribuyen en el wheel o se muestran al leer el repositorio.

La limpieza retirará ese producto anterior del árbol actual. Git será el único archivo
histórico. Una instalación nueva y una lectura del código, la interfaz, los ejemplos, las
pruebas y la documentación deben describir solamente el dominio financiero y la
infraestructura transversal necesaria para operarlo.

## Objetivos

- Eliminar datasets, resultados, documentos, ejemplos y componentes de interfaz de SRE,
  AWS operacional, Kubernetes, incidentes y remediación.
- Retirar código muerto y compatibilidad de ejecución o persistencia exclusiva del producto
  anterior.
- Conservar y aclarar la arquitectura multiagente financiera basada en DeepAgents y
  LangGraph.
- Mantener funcionando análisis, chat, cartera paper, órdenes con aprobación, backtesting,
  flujos, API compatible con la aplicación de referencia y evaluaciones financieras.
- Hacer ejecutable la frontera: una prueba impedirá que vuelvan a distribuirse superficies
  no financieras conocidas.
- Dejar un repositorio comprensible sin directorios históricos ni resultados generados
  versionados.

## No objetivos

- Cambiar las decisiones financieras, prompts de personas inversoras, modelos cuantitativos
  o contratos de compatibilidad con la aplicación financiera de referencia.
- Quitar infraestructura transversal necesaria: HTTP seguro, SQLite/Postgres, Docker local,
  MCP, proveedores LLM, autenticación, auditoría, scheduler de mercado o trazas.
- Quitar Slack o Telegram: sus únicas superficies conservadas entregan análisis y alertas de
  mercado financieras.
- Quitar detección de secretos sólo porque una credencial tenga formato de un proveedor de
  nube. La prevención de filtraciones es seguridad transversal, no una integración AWS.
- Reescribir el historial Git ni garantizar la migración automática de bases de datos del
  producto SRE. El usuario aprobó expresamente abandonar esa compatibilidad.

## Restricciones

- El corpus financiero de equities y las pruebas/evaluaciones financieras deben conservarse.
- Los nombres públicos exigidos por la compatibilidad financiera de la aplicación de
  referencia se mantienen aunque sean históricos, y continúan protegidos por su allowlist.
- Las noticias financieras pueden mencionar AWS, investigaciones regulatorias o incidentes
  empresariales cuando sean evidencia de mercado; no representan capacidades operacionales.
- No se editarán ni borrarán datos locales de usuario en `.fin-graph/`.
- La aplicación que está ejecutándose en el puerto 8000 no se detendrá para esta limpieza;
  las verificaciones Python usarán el entorno ya instalado sin resincronizar el ejecutable.

## Enfoques considerados

### 1. Limpieza estricta — elegido

Eliminar los residuos del árbol actual y conservarlos sólo en Git. Produce una superficie
coherente y permite una protección automatizada fuerte. Es el enfoque elegido porque el
usuario requiere que todo quede dentro del ámbito financiero y acepta abandonar la
compatibilidad con bases antiguas.

### 2. Archivo depreciado dentro del repositorio — descartado

Mover el material a `legacy/` o `docs/archive/` facilitaría consultarlo, pero seguiría
distribuyendo y mostrando el dominio que se quiere retirar.

### 3. Deprecación gradual — descartado

Mantener shims y advertencias reduciría el riesgo para instalaciones SRE antiguas, pero
añadiría complejidad sin valor para el producto financiero nuevo.

## Alcance de la limpieza

### Datos y artefactos

- Eliminar por completo `projects/sre-better-harness/` y
  `projects/sre-deepagents/`: contienen 352 archivos de resultados generados y pruebas EKS/RDS.
- Eliminar `src/fin_graph/evals/data/rds_upstream/` y el manifiesto obsoleto
  `src/fin_graph/evals/data/manifest.json`, que enumera 276 archivos del corpus SRE aunque
  casi todos ya no existen.
- Conservar `src/fin_graph/evals/data/equities/` y los casos financieros usados por el
  harness actual.
- Añadir reglas de ignore para evitar volver a versionar resultados locales equivalentes.

### Código Python y persistencia

- Simplificar `fin_graph.domain.window` para que contenga únicamente `AnalysisWindow` y sus
  constantes financieras. Retirar `IncidentWindow`, `WindowSource`, `resolve_window` y sus
  pruebas sin consumidores productivos.
- Retirar el namespace heredado `remediation:<thread>` de limpieza de checkpoints y de sus
  pruebas. Mantener los namespaces financieros `analysis`, `chat` y `orders`.
- Neutralizar descripciones históricas de migraciones sin alterar las tablas genéricas que
  el producto financiero sí utiliza.
- Cambiar ejemplos de herramientas, identificadores y fixtures de pruebas (`pager`, pods,
  bases `sre`, comandos `incident-*`) por equivalentes financieros que sigan probando el
  mismo comportamiento técnico.
- Limpiar comentarios o nombres que comparan categorías financieras con diagnóstico de
  incidentes.

### Interfaz web

- Eliminar `InvestigationContent.tsx`, su CSS y su prueba: no está importado por la aplicación
  financiera y todavía representa diagnóstico, causa raíz y remediación.
- Confirmar que el bundle sólo expone construcción de estrategias, ejecución multiagente,
  análisis, cartera, órdenes y backtests.
- Mantener términos como “razonamiento”, “evidencia” y “hallazgos” cuando describen el análisis
  financiero; no son exclusivos del producto anterior.

### Ejemplos y paquete de workflows

- Retirar el piloto HTML SRE y configuraciones IDE/run históricas, o sustituir cualquier
  ejemplo todavía útil por uno explícitamente financiero.
- Convertir los casos de ejemplo de `packages/workflow` que usan manifiestos de incidentes en
  casos de research financiero, conservando la demostración del motor genérico.
- Actualizar `examples/README.md` para enlazar solamente ejemplos existentes y financieros.

### Documentación

- Eliminar especificaciones, planes, progresos e informes del producto SRE de octubre, además
  de la documentación Wave2 y el inventario anterior de retirada.
- Conservar documentación financiera, de DeepAgents, compatibilidad, harness financiero,
  operación local y despliegue genérico.
- Revisar enlaces desde README y documentos conservados para que no apunten a material
  eliminado.
- No crear un archivo `legacy`; el commit y la historia Git documentan la retirada.

### Frontera automatizada

La prueba existente se reemplazará por una frontera llamada en términos financieros. Debe
comprobar como mínimo:

- ausencia de rutas de producto y datasets SRE conocidos;
- ausencia de dependencias operacionales como `boto3` y el SDK de Kubernetes;
- ausencia de comandos y rutas HTTP del producto anterior;
- ausencia de artefactos generados bajo `projects/sre-*`;
- importabilidad de las superficies financieras principales;
- ausencia de componentes web de remediación.

La prueba usará una allowlist pequeña para menciones legítimas: patrones de secretos,
noticias financieras y documentación estrictamente necesaria de seguridad. No se usará una
lista amplia que pueda ocultar nuevas integraciones no financieras.

## Comportamiento resultante

El flujo principal no cambia:

1. Una petición financiera entra por CLI, API, chat, scheduler de mercado o la capa de
   compatibilidad.
2. El workflow financiero obtiene datos de mercado y delega en los agentes/personas
   configurados.
3. Se sintetiza y verifica un informe con evidencia financiera.
4. Opcionalmente se entrega el informe o se propone una orden paper sujeta a controles.
5. El harness evalúa exclusivamente escenarios financieros.

No existirá una ruta paralela para alertas de infraestructura, diagnóstico de incidentes,
consultas CloudWatch/Kubernetes ni remediación operacional.

## Verificación y criterio de aceptación

- Búsqueda exhaustiva de rutas y términos operacionales en archivos versionados, clasificando
  explícitamente cualquier coincidencia legítima restante.
- Prueba de frontera finance-only.
- Suite completa de Python con el intérprete del entorno existente.
- Ruff, MyPy e import-linter.
- Pruebas y build de la interfaz web.
- Validación del corpus y harness financiero, incluida la lista esperada de casos.
- Comprobaciones de compatibilidad financiera y snapshots SQLite existentes.
- `git status` sólo debe contener los cambios intencionales de esta limpieza.

La limpieza se considera terminada cuando no queda funcionalidad, dataset, ejemplo, interfaz
o documentación SRE/AWS operacional en el árbol actual; toda coincidencia textual restante
debe estar justificada por seguridad o por evidencia financiera.

## Decisiones dejadas al implementador

- Elegir si un ejemplo técnico útil se reescribe en contexto financiero o se elimina, según
  su cobertura única y sus referencias actuales.
- Agrupar eliminaciones documentales en la forma más clara sin conservar archivos vacíos.
- Ajustar nombres internos no públicos cuando mejore la claridad, siempre que no cambie los
  contratos financieros protegidos.
