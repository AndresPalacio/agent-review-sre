# Reemplazo compatible de AI Hedge Fund sobre Fin Graph

Fecha: 2026-11-03

## Intención

`fin-graph` debe poder sustituir la copia local de `ai-hedge-fund` situada en
`C:\Users\Usuario\Desktop\ai-hedge-fund`. Un consumidor debe poder cambiar el proceso al que se
conecta y seguir usando la aplicación web, HTTP/SSE, CLI y las importaciones Python publicadas por
la referencia con los mismos contratos y resultados funcionales.

La implementación no será una segunda aplicación incrustada. El dominio, la seguridad, los datos,
los cálculos y la persistencia vivirán en `fin_graph`; una fachada de compatibilidad conservará los
contratos externos de AI Hedge Fund. La orquestación de lenguaje se construirá con Deep Agents y
LangGraph, y el LLM nunca decidirá ni ejecutará directamente una operación.

## Alcance observado en la referencia

La paridad abarca comportamiento existente y ejecutable, aunque la propia referencia lo llame
experimental. No abarca casillas futuras sin implementación de `ROADMAP.md`.

1. La aplicación v1: diecinueve analistas seleccionables, gestor de riesgo, gestor de cartera,
   proveedores Yahoo y Financial Datasets, caché, CLI, visualización y backtester long/short.
2. El backend FastAPI: salud, ejecución y backtest con SSE, catálogo de agentes y modelos, claves
   de proveedor, Ollama, almacenamiento JSON, CRUD de flujos, historial de corridas y ciclos.
3. El workbench React: editor de nodos y conexiones, flujos guardados, pestañas, ejecución,
   backtesting, salida/razonamiento/problemas, selección de modelos y administración de Ollama y
   claves.
4. El motor v2: contratos point-in-time, clientes y caché de datos, snapshots, ocho modelos alpha
   visibles (`pead`, `buffett`, `candlestick`, `news_sentiment`, `technical_ml`,
   `technical_scan`, `arima` y `lstm`) más Monte Carlo, consenso, sizing, broker paper, riesgo
   Monte Carlo, event studies y backtesting por modelo.
5. Estrategias y algoritmos: SMA crossover, RSI mean reversion y momentum; catálogo, jobs de
   backtest, cancelación, señales paginadas, artefactos y eventos SSE.
6. El catálogo completo de skills bajo `app/backend/skills`, incluidos documentos, referencias,
   scripts y recursos vendorizados de Vibe-Trading.
7. Los contratos comprobados por las pruebas de la referencia, incluidos errores, rate limiting,
   selección de proveedor, alias de ticker, formatos SSE y rutas v2.

## Objetivos

- La UI original debe poder apuntar a `http://localhost:8000` y funcionar sin cambios.
- Clientes HTTP existentes deben conservar rutas, métodos, status codes, cabeceras, JSON y SSE.
- Los entrypoints y comandos existentes deben aceptar los mismos argumentos y producir datos
  equivalentes, además de conservar `fin-graph` como interfaz nativa.
- Las importaciones Python usadas por los entrypoints y documentadas por la referencia tendrán
  shims delgados sobre el núcleo nativo.
- Con los mismos fixtures, fecha, seed, configuración y respuestas de modelo, los resultados
  estructurados y numéricos deben ser iguales o estar dentro de la tolerancia declarada por el
  algoritmo original.
- Deben mantenerse las capacidades actuales de `fin-graph`: análisis durable, chat Deep Agents,
  evidencia, aprobaciones, órdenes paper, scheduler, gateways, evaluaciones y harness.
- El producto final no debe necesitar el árbol `ai-hedge-fund` en tiempo de ejecución.

## No objetivos

- Implementar elementos de `ROADMAP.md` que no tienen código funcional en la referencia, como
  trading real, CIO autónomo, auto-promoción de estrategias o nuevos conectores alternativos.
- Garantizar texto idéntico de un proveedor generativo en llamadas en vivo cuando el proveedor no
  ofrece ejecución determinista. Se preservarán prompt, modelo, temperatura y forma estructurada;
  con respuestas grabadas el resultado sí debe ser exacto.
- Habilitar dinero real. Todas las operaciones seguirán siendo paper y las mutaciones continuarán
  sujetas a límites y aprobación humana.
- Copiar secretos, bases locales, cachés, `__pycache__` o `.env` de la referencia.
- Importar flujos, historial, jobs, artefactos o claves desde instalaciones anteriores. El reemplazo
  parte de almacenamiento limpio y la compatibilidad se aplica a datos creados desde su arranque.

## Antiobjetivos

- Dos motores financieros que puedan divergir.
- Rutas de compatibilidad que omitan validación o controles de riesgo para imitar un defecto.
- Simular éxito cuando faltan datos, modelos o dependencias; se conservará la semántica
  fail-loud de v2.
- Introducir lookahead en análisis históricos o backtests.
- Hacer obligatorios TensorFlow u otros paquetes pesados para quien solo usa el producto base.
- Mantener el dominio SRE legado después de acreditar la paridad y migrar sus últimos tests.

## Restricciones y decisiones del usuario

- La compatibilidad es de sustitución, no solo equivalencia conceptual.
- Deben funcionar web, API, SSE, CLI e integración Python.
- Se reproduce todo comportamiento implementado en `src/`, `app/` y `v2/`; se excluye solamente
  el roadmap aún no implementado.
- La arquitectura elegida es integración nativa con fachada compatible.
- Python continúa en 3.12; React continúa en el proyecto `web/`; Deep Agents 0.7 y LangGraph 1
  siguen siendo las primitivas de agentes.
- Se conservan las licencias y notices MIT de AI Hedge Fund y Vibe-Trading dentro del producto
  Apache-2.0.
- La instalación comienza desde cero. `app/backend/hedge_fund.db` y cualquier otro estado de la
  referencia no se leen, migran ni modifican.

## Baseline y decisiones de construcción

- El baseline de `fin-graph` es el commit
  `f4c701a58e484d259816904ae85dc717f2967966`, más este diseño. La referencia local no contiene
  metadatos Git; el primer corte generará un manifiesto SHA-256 de todos sus archivos relevantes,
  excluyendo `.env`, bases de datos, cachés, entornos, bytecode y dependencias instaladas. Ese
  snapshot queda congelado durante la migración.
- Las tolerancias no se escogerán globalmente. El manifiesto registrará por salida: igualdad exacta
  para dinero, ids, decisiones y datos discretos; tolerancia de los tests originales para floats;
  y, si no existe, `abs <= 1e-9`/`rel <= 1e-7` para cálculo determinista. Monte Carlo compara con
  seed fijo; ML compara predicción/artefacto con versiones fijadas y tolerancia empírica aprobada
  por el test diferencial antes de portar el modelo.
- Desarrollo y CI no presuponen credenciales. Toda frontera externa tendrá fixtures grabados y
  clientes falsos; Financial Datasets, Yahoo, Ollama y proveedores LLM tendrán smokes optativos
  cuando el operador aporte credenciales. Ningún secreto de la referencia se inspecciona ni copia.
- Se soportan dos perfiles: local compatible directo, y remoto autenticado detrás o no de proxy.
  El remoto define proxies confiables explícitos; no confía en `X-Forwarded-*` por defecto. Exponer
  la API heredada sin autenticación fuera de loopback requiere el opt-in inseguro ya descrito.
- Antes de vendorizar skills o assets, el primer corte producirá un inventario de licencia por
  archivo/origen. Solo se distribuye material MIT/Apache compatible y con sus notices; cualquier
  recurso sin permiso verificable se reemplaza o se excluye del artefacto hasta resolverlo.

## Alternativas consideradas

### 1. Integración nativa con fachada compatible (elegida)

Cada capacidad se expresa en contratos de `fin_graph` y las superficies heredadas solo adaptan
entrada, salida y eventos. Requiere más trabajo inicial, pero deja una sola fuente de verdad,
permite usar Deep Agents y conserva las salvaguardas actuales.

### 2. Vendorizar AI Hedge Fund y envolverlo

Produciría paridad rápida en algunas rutas, pero conservaría dos grafos, dos almacenes, dos
catálogos de modelos y dos motores de cartera. Los resultados divergirían con el tiempo y las
aprobaciones actuales podrían evitarse por las rutas heredadas.

### 3. Reescribir también las capacidades actuales

Daría una arquitectura nueva uniforme, pero volvería a implementar seguridad, scheduler,
gateways, ledger y evaluaciones que ya están probados. Aumenta el riesgo sin mejorar la
compatibilidad.

## Definición de compatibilidad

La compatibilidad se congela antes de implementar mediante un manifiesto versionado. Cada entrada
incluye método de conexión, request, response, errores y oracle.

| Superficie | Contrato que se conserva | Criterio |
|---|---|---|
| HTTP | Rutas sin versión de v1 y rutas `/v2`; métodos, status, JSON y cabeceras | OpenAPI y contract tests contra ambos servidores |
| SSE | Nombres de evento, orden, JSON por línea, terminales y errores | Transcripción byte a byte con modelo guionizado |
| UI | Editor, nodos, flujos, ejecución, backtest y settings | La UI de referencia pasa E2E contra el backend nuevo |
| CLI | Argumentos, alias, selección de analistas/modelos, salida y exit codes | Golden tests de Typer/subprocesos |
| Python | Imports y funciones usados por entrypoints/documentación | Tests de importación y firma |
| Datos | Point-in-time, orden, cache key, errores y normalización | Fixtures compartidos y pruebas de fecha límite |
| Cálculo | Señal, confianza, riesgo, cartera, P&L y métricas | Igualdad exacta o tolerancia numérica registrada |
| LLM | Catálogo, provider/model, prompt, formato y fallback | Igualdad con modelo guionizado; equivalencia estructural en vivo |
| Persistencia | Flujos, runs, ciclos, claves, jobs y artefactos | Reinicio, migración e idempotencia |

La fachada aceptará las variables de entorno de ambos productos. Los nombres modernos tienen
precedencia y un conflicto explícito produce error, nunca una elección silenciosa.

## Arquitectura

```text
UI original / UI fin-graph / clientes / CLI / imports Python
                            |
             superficies nativas y de compatibilidad
                            |
      normalización de requests, eventos y modelos de salida
                            |
    motor único: datos -> herramientas -> Deep Agents -> riesgo
                            |
      backtest / ledger paper / flujos / artefactos / memoria
                            |
          SQLite local o Postgres durable y distribuido
```

### 1. Núcleo financiero y analistas

Los seis analistas actuales se ampliarán hasta el catálogo de diecinueve de la referencia. Cada
identidad será un `SubAgent` Deep Agents con prompt propio y acceso solo a tools read-only de sus
dominios. Los cálculos cuantitativos se portarán como funciones deterministas y serán el oracle de
señal/confianza; el modelo redacta y explica, pero no puede contradecir los límites.

El gestor de cartera será el supervisor. Riesgo, acciones permitidas, sizing y fills permanecen en
código determinista. La selección de analistas de v1 se traduce a subagentes disponibles en esa
corrida. El modo nativo actual puede usar el mismo catálogo sin adoptar los nombres de respuesta
heredados.

### 2. Datos, features y modelos v2

Se unificarán los modelos `Price`, fundamentals, noticias e insiders, conservando adaptadores para
las formas v1/v2. Yahoo y Financial Datasets implementarán un protocolo común, caché point-in-time,
rate limiting y errores distinguibles entre ausencia de datos y fallo de infraestructura.

Los modelos v2 se portarán a `fin_graph.quant`/`fin_graph.strategies` con el mismo registro y
parámetros. NumPy/Pandas/SciPy/scikit-learn/statsmodels y dependencias equivalentes vivirán en un
extra `quant`; LSTM vivirá en un extra `ml`. La API devuelve un error compatible y accionable si se
invoca una capacidad cuyo extra no está instalado. La distribución de la aplicación completa instala
ambos extras.

Todos los entrenamientos y simulaciones fijarán seed cuando la biblioteca lo permita. Los artefactos
incluyen versión de algoritmo, features, rango de datos y seed.

### 3. Backtesting, event studies y estrategias

Habrá un motor de simulación compartido con adaptadores para:

- el backtester v1 que ejecuta el conjunto de analistas por fecha;
- el backtester v2 de un alpha model;
- el backtester de estrategias algorítmicas y sus jobs asíncronos.

El motor reutiliza la contabilidad actual de fills, habilitando fills parciales solo en simulación.
No ve información posterior a la fecha simulada. Produce las métricas, benchmark, snapshots,
señales y eventos esperados por cada contrato heredado. Event studies y riesgo Monte Carlo son
servicios puros sobre el mismo almacén point-in-time.

### 4. Compilador de flujos

El JSON del editor de AI Hedge Fund se valida y compila, no se interpreta mediante `eval` ni imports
arbitrarios. Los nodos de analista se convierten en subagentes; portfolio/risk/output son pasos
tipados; las aristas determinan dependencias y fan-out. Los componentes desconocidos producen el
mismo error público de la referencia con un código interno seguro.

Cada ejecución guarda revisión del flujo, modelo, inputs, ciclos, eventos y resultado. Una edición no
cambia corridas históricas. Cancelar es cooperativo y termina con un evento terminal único.

### 5. HTTP, SSE y seguridad

El servidor mantendrá `/v1/*` y añadirá los routers exactos de la referencia: `/hedge-fund`,
`/flows`, `/api-keys`, `/ollama`, `/language-models`, `/storage`, `/v2/skills`, `/v2/algorithms`,
`/v2/backtests`, `/v2/{model}/...` y `/v2/risk/...`, además de salud y ping.

En loopback, el perfil de compatibilidad funciona como la aplicación local original y no exige la
clave de autenticación de `fin-graph`. Si se intenta exponer en una interfaz no-loopback, el arranque
rechaza las rutas heredadas sin autenticación salvo consentimiento explícito mediante
`ALLOW_INSECURE_LEGACY_API=true`. Las rutas nativas conservan roles. Las claves de proveedores se
guardan cifradas y nunca se incluyen en eventos o logs.

SSE se adaptará desde un bus interno único a los dos dialectos públicos. Backpressure, desconexión,
replay e idempotencia siguen los mecanismos actuales.

### 6. Persistencia y ejecución local

Se definirán puertos de almacenamiento para flujos, runs, ciclos, claves de proveedor, jobs y
artefactos. SQLite será el backend local compatible, con locks en proceso; Postgres seguirá siendo
el backend durable multi-réplica, con advisory locks y notificación. Las capacidades distribuidas que
SQLite no puede prometer se anuncian en readiness, no se simulan.

Se añadirá un comando de aplicación local y scripts equivalentes a `app/run.bat`/`run.sh`. Levantará
backend, base SQLite y frontend/estáticos en los puertos esperados. Docker instala el perfil completo.

### 7. UI

El workbench visual reemplazará la consola SRE de `web/`. Se portará el comportamiento de la UI de
referencia a React/TypeScript actual, manteniendo su disposición, nodos y acciones; se reutilizarán
assets MIT cuando reduzcan riesgo. La capa API tendrá base URL configurable y no fijará
`localhost` dentro de componentes.

La UI también podrá mostrar las capacidades nativas que no existen en la referencia (aprobaciones,
paper ledger y explicación), sin cambiar los flujos heredados. Esas extensiones quedarán en vistas o
pestañas separadas.

### 8. CLI e imports Python

`fin-graph` gana comandos para run, backtest, modelos, algoritmos, event study y app. Se conservarán
los entrypoints compatibles y shims finos para las importaciones públicas usadas por la referencia,
incluidos `src.main`, `src.backtesting`, `v2.*` y `app.backend.main:app`. Los shims no contendrán
lógica financiera; delegan y traducen modelos.

La compatibilidad Python se limita a símbolos importados por entrypoints, documentación y tests de
la referencia. Módulos privados no documentados se registran en el manifiesto como no públicos.

### 9. Skills

El catálogo completo se incorpora preservando estructura y notice de Vibe-Trading. Un loader
valida paths, frontmatter, tamaño y MIME, evita traversal y entrega recursos por la API compatible.
Deep Agents recibe las skills aplicables mediante `SkillsMiddleware`; los scripts no se ejecutan por
el mero hecho de visualizar o cargar una skill.

## Flujo de una ejecución compatible

1. La fachada valida la request heredada y resuelve aliases de ticker, fechas, modelo y claves.
2. El compilador resuelve el flujo guardado o construye el flujo v1 predeterminado.
3. El supervisor Deep Agent despacha los analistas seleccionados con contexto y tools aislados.
4. Cada analista obtiene datos point-in-time y produce componentes deterministas más narrativa.
5. Riesgo calcula límites; el gestor de cartera produce la decisión dentro de esos límites.
6. El bus guarda y emite progreso; el adaptador SSE lo expresa en el dialecto solicitado.
7. El adaptador de salida devuelve exactamente el esquema v1/v2 o el esquema nativo.
8. Si se solicita una operación, solo se crea una propuesta paper y el flujo de aprobación actual
   conserva la autoridad de ejecución.

## Errores y degradación

- Validación de cliente conserva status y cuerpo heredados; internamente se adjunta un correlation
  id sin filtrar secretos.
- Falta de datos genuina puede producir señal neutral/no-data como en el oracle; fallo de proveedor
  no se convierte en señal neutral.
- Un analista fallido queda visible en progreso y resultado. El supervisor puede terminar los demás,
  pero nunca inventa la salida faltante.
- Extras ausentes, modelo no disponible y Ollama detenido tienen errores tipados y acciones de
  remediación compatibles.
- Un stream siempre termina una sola vez en `complete`, `error` o `cancelled`.
- Las rutas de mutación son idempotentes por sus identificadores públicos.

## Migración por cortes verificables

1. **Oracle y manifiesto:** ejecutar las pruebas offline de la referencia, congelar OpenAPI, SSE,
   fixtures, CLI e imports; construir el runner diferencial.
2. **Fundaciones compatibles:** aliases de configuración, proveedores/modelos, almacenamiento
   SQLite/Postgres y shell de routers/shims.
3. **Paridad v1:** diecinueve analistas Deep Agents, riesgo/cartera, ejecución y backtester v1.
4. **Paridad v2:** datos, features, modelos alpha/ML, consenso, Monte Carlo, event studies y broker.
5. **Estrategias y recursos:** algoritmos, jobs, artefactos, catálogo completo de skills.
6. **Superficies:** contratos HTTP/SSE, CLI, imports, scripts de arranque y Docker.
7. **Workbench web:** editor de flujos, settings, ejecución y backtesting; extensiones nativas
   separadas.
8. **Convergencia:** migrar tests restantes, eliminar dominio/datasets SRE y dependencias sin uso,
   actualizar documentación y empaquetado.

Cada corte debe producir software ejecutable y pasar su suite diferencial antes de continuar. La
eliminación SRE ocurre al final, nunca antes de que sus reemplazos estén acreditados.

## Estrategia de pruebas

### TDD y oracle diferencial

Cada capacidad empieza con una prueba que falla en `fin-graph`. El runner ejecuta la misma entrada
contra la referencia y el reemplazo usando reloj, red y modelo guionizados. Las respuestas se
normalizan solo para campos deliberadamente no deterministas como ids generados y timestamps.

No se importará la referencia desde el producto. Los golden fixtures generados incluyen procedencia,
hash del commit/estado fuente y licencia.

### Capas de verificación

- Unitarias para matemáticas, fechas point-in-time, parsing, serialización y adaptadores.
- Contract tests para todas las rutas, schemas, status codes y errores.
- Transcripciones SSE de run, backtest, cancelación y fallo parcial.
- Pruebas de persistencia y reinicio en SQLite y Postgres.
- Pruebas de imports/entrypoints en un wheel limpio.
- Vitest para componentes y Playwright para crear, guardar, duplicar, ejecutar y backtestear un
  flujo, cambiar modelo y gestionar Ollama.
- E2E adicional: compilar la UI de referencia sin modificar su cliente y ejecutarla contra el
  backend nuevo.
- Suite completa existente para no perder análisis, chat, aprobaciones, scheduler, gateways,
  harness y workflow package.
- Smokes optativos con Yahoo, Financial Datasets, Ollama y proveedores LLM reales. No forman parte
  del gate offline sin credenciales.

## Criterios de aceptación

1. El manifiesto no contiene contratos funcionales de la referencia pendientes.
2. Todas las pruebas offline de la referencia aplicables pasan contra la fachada nueva; cualquier
   exclusión está ligada a una funcionalidad no implementada en la propia referencia.
3. La UI original completa el flujo E2E contra el backend nuevo sin cambios de código.
4. La UI nueva ofrece el mismo recorrido y no contiene superficies SRE.
5. Los backtests v1, v2 y de algoritmos reproducen los golden results con la tolerancia fijada.
6. El wheel limpio ofrece `fin-graph`, los entrypoints compatibles y todos los imports públicos.
7. SQLite permite la experiencia local de un comando; Postgres conserva reinicio y concurrencia.
8. Ninguna ruta puede colocar una orden real ni evitar el gate de riesgo/aprobación.
9. Ruff, formato, mypy, import-linter, pytest, build web, Vitest y Playwright pasan.
10. README, `.env.example`, OpenAPI, Docker y notices describen el producto final y sus límites.

## Llamadas del implementador

- Preferir adaptadores y ports pequeños a copiar servicios completos de la referencia.
- Mantener el extra base liviano; `app`/Docker instalan el conjunto completo para garantizar la
  experiencia de reemplazo.
- Preservar nombres públicos aunque internamente se usen modelos más estrictos.
- Tratar la copia local indicada por el usuario como oracle de comportamiento durante el desarrollo,
  sin modificarla ni leer/copiar su `.env`.
- Si una prueba revela un defecto inseguro de la referencia, conservar el contrato observable salvo
  la conducta insegura; documentar la desviación y cubrir la salvaguarda con una regresión.

## Evidencia final verificada (2026-11-03)

- El manifiesto ejecutable cierra 39 contratos: 39 verificados, cero exclusiones y cero pendientes,
  contra el snapshot `42cd3a84c47ccd9ca2a891146a2d818b2ea8c7908162ef107975239b7148a71f`.
- La aceptación completa pasa 14/14 criterios tanto en SQLite como en PostgreSQL real y ambos
  producen el digest financiero público
  `a9036a91c42b90abde555aec416592908ce6e3da95266d13410332cc3f983f9b`.
- El frontend, wheel y contenedor se construyeron desde limpio; el contenedor corre sin privilegios,
  conserva `/data` y responde `/health`.
- El checker `scripts/check_compatibility_manifest.py` invalida owners inexistentes, evidencia en
  prosa, snapshots/candidatos obsoletos, estados pendientes, ownership duplicado y exclusiones sin
  aprobación de diseño.
- La evidencia y los límites reproducibles quedan publicados en
  `docs/compatibility/parity-report.md`; las pruebas con proveedores vivos siguen siendo smokes
  opcionales y no sustituyen el gate offline.
