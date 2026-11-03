# Diseño: organización comprensible del sistema multiagente

## Intención

Reorganizar `fin-graph` para que una persona nueva pueda responder, siguiendo el árbol de código,
cinco preguntas sin buscar símbolos por todo el repositorio:

1. ¿Qué agentes existen?
2. ¿Cuáles usan DeepAgents y cuáles son pasos controlados por LangGraph?
3. ¿Quién supervisa o delega en quién?
4. ¿Qué herramientas, límites y middleware tiene cada agente?
5. ¿Qué parte es producción y qué parte es evaluación u optimización opcional?

La reorganización no cambia decisiones financieras, contratos HTTP, formatos de persistencia ni la
compatibilidad con AI Hedge Fund. Es una refactorización estructural y documental protegida por la
suite existente.

## Problema actual

El comportamiento multiagente está implementado, pero sus responsabilidades están mezcladas:

- `fin_graph/agents` contiene bloques genéricos, pero no contiene todos los agentes DeepAgents.
- Las diecinueve personas financieras se definen en `domain/analysts.py`, se convierten en
  subagentes en `graphs/analysis/personas.py` y se ensamblan en `graphs/analysis/compat.py`.
- El supervisor conversacional vive en `graphs/chat/graph.py`.
- El solver verificado vive parcialmente en `work/graphs.py`.
- `harness/runtime.py` es cableado de producción, aunque el nombre `harness` sugiere una herramienta
  experimental.
- El mismo paquete `harness` contiene instrumentación necesaria en producción y el optimizador
  opcional `better-harness`.
- `harness/fin.toml` parece configuración necesaria para arrancar la aplicación, aunque solo sirve
  para optimización.
- `docs/arquitectura-agentes.md` todavía describe el producto SRE anterior y referencias que ya no
  existen.

El resultado funciona, pero el árbol no enseña la arquitectura.

## Objetivos

- Hacer que el árbol de paquetes represente la arquitectura real.
- Centralizar el catálogo ejecutable de agentes y supervisores.
- Separar actores de razonamiento, workflows, cableado y optimización.
- Identificar explícitamente cada uso de `create_deep_agent`.
- Mantener una única fuente de verdad para las diecinueve identidades financieras.
- Añadir documentación corta junto al código, con recorridos desde API/CLI hasta agente y salida.
- Mantener sin cambios los contratos públicos de AI Hedge Fund y los resultados de los evals.
- Hacer explícito que `optimization/fin.toml` es opcional y nunca se carga al arrancar la app.

## No objetivos

- No rediseñar prompts, herramientas, scoring ni decisiones de inversión.
- No reemplazar LangGraph ni DeepAgents.
- No añadir una pantalla nueva a la interfaz en esta fase.
- No convertir todos los pasos deterministas en agentes.
- No presentar el optimizador `better-harness` como runtime de producción.
- No conservar imports internos antiguos que no sean parte de una superficie pública documentada.

## Antiobjetivos

La refactorización se considerará fallida si introduce módulos duplicados con la misma lógica,
catálogos declarativos que divergen del código ejecutado, reexportaciones indefinidas que ocultan la
implementación real o cambios de comportamiento mezclados con movimientos de archivos.

## Enfoques considerados

### 1. Reorganización física por responsabilidades — seleccionado

Mover implementaciones a paquetes que representen su papel: agentes, workflows, runtime y
optimización. Actualizar todos los imports internos y conservar solo los adaptadores públicos que
exige la compatibilidad de producto.

Ventajas: el código enseña la arquitectura; elimina el significado ambiguo de `harness`; reduce la
distancia entre catálogo, fábrica y documentación. Coste: diff grande y muchos imports actualizados.

### 2. Fachada nueva sobre módulos existentes

Crear `agents/catalog.py` y puntos de entrada claros, pero dejar implementaciones bajo `graphs`,
`harness` y `work`.

Ventajas: menor riesgo inmediato. Desventajas: al seguir un símbolo, la persona vuelve a encontrar
la dispersión actual; crea una segunda arquitectura nominal sin corregir la física.

### 3. Solo documentación

Actualizar diagramas y guías sin mover código.

Ventajas: cambio pequeño. Desventajas: no satisface el objetivo de que el propio código sea fácil de
entender y la documentación volvería a quedar expuesta a divergencias.

## Arquitectura destino

```text
src/fin_graph/
  trading/
    __init__.py
    order_desk.py

  agents/
    README.md
    catalog.py
    common/
      factory.py
      subagents.py
      middleware/
      testing/
    analysis/
      README.md
      catalog.py
      personas.py
      persona_prompts.py
      supervisor.py
    chat/
      README.md
      supervisor.py
    solver/
      README.md
      control.py
      supervisor.py
      testing.py
    optimizer/
      README.md
      proposer.py

  workflows/
    README.md
    analysis/
      graph.py
      state.py
      services.py
      evidence.py
      snapshots.py
      prompts.py
      extensions.py
      strategy/
    orders/
      graph.py
    market_watch/
      graph.py

  runtime/
    README.md
    analysis.py
    observation.py
    profile.py
    trace.py

  optimization/
    README.md
    harness/
      artifacts.py
      configuration.py
      dataset.json
      dataset.py
      execution.py
      experiment.py
      proposer.py
      quality.py
      runner.py
      surfaces.py
      worker.py

optimization/
  README.md
  fin.toml
```

### Regla de dependencia

La dirección de dependencias será:

```text
domain/safety/integrations
       ↑
trading
       ↑
agents/common
       ↑
agents especializados
       ↑
workflows
       ↑
runtime
       ↑
CLI / API / Studio

optimization → runtime + evals, nunca al revés
```

`trading/order_desk.py` será el servicio compartido de reglas y ejecución paper que consumen chat y
el workflow de órdenes. El solver mantendrá sus helpers de control y su modelo offline dentro de
`agents/solver`, por lo que ningún agente dependerá de `workflows` ni de `work`.

Producción no importará `fin_graph.optimization`. La dependencia `better-harness` seguirá siendo un
extra opcional. La app, la API y los evals normales no leerán `optimization/fin.toml`.

## Catálogo único y navegable

`fin_graph.agents.catalog` expondrá descriptores inmutables de los sistemas agentivos, no una copia
de sus prompts. Cada `AgentDescriptor` contendrá:

- identificador estable;
- nombre legible;
- tipo: `deepagents_supervisor`, `deepagents_subagent` o `langchain_agent`;
- propósito;
- fábrica real como import path;
- hijos o personas delegables;
- política de herramientas (`read_only`, `paper_trade`, `workspace`);
- middleware y límites relevantes;
- condición de activación y superficie que lo usa.

El catálogo no registrará nodos deterministas de LangGraph: esos pertenecen al mapa de
`workflows`. Cada subpaquete de agentes declarará sus descriptores junto a sus fábricas y
`agents/catalog.py` los agregará. El runtime conservará imports explícitos y tipados de las fábricas;
no construirá producción mediante imports dinámicos tomados de strings. Las pruebas comprobarán que
el callable declarado por cada descriptor existe y que toda fábrica que usa `create_deep_agent`
aparece exactamente una vez. Así el catálogo es la fuente autoritativa de descubrimiento sin crear
una segunda ruta de ensamblaje.

Las diecinueve identidades financieras seguirán teniendo una única fuente de verdad de dominio. El
catálogo de agentes las referenciará y añadirá metadatos de ejecución; no repetirá nombres, prompts o
dominios manualmente. Una prueba recorrerá el catálogo y resolverá cada fábrica para impedir entradas
obsoletas.

El CLI añadirá comandos de solo lectura:

```text
fin-graph agents list
fin-graph agents describe <id>
```

La salida se derivará del catálogo usado por el código. Esto permite descubrir la arquitectura sin
abrir la interfaz web y sin crear otra fuente de verdad.

## Responsabilidad de cada capa

### `agents`

Contiene componentes que toman decisiones mediante un modelo o que se registran como subagentes.
Cada llamada a `create_deep_agent` deberá vivir bajo este paquete. `agents/analysis/supervisor.py`
ensamblará el supervisor compatible y sus diecinueve personas; `agents/chat/supervisor.py` el chat;
`agents/solver/supervisor.py` el solver verificado; y `agents/optimizer/proposer.py` construirá el
DeepAgent opcional que edita el workspace del optimizador. Middleware, fábricas y modelos guionados
comunes vivirán en `agents/common`. La fábrica del proponente no importará `better_harness` ni
`fin_graph.optimization`: recibirá el modelo, prompt y directorio ya resueltos para conservar la
dirección de dependencias.

### `workflows`

Contiene máquinas de estado y transiciones controladas por código. Un nodo puede invocar un agente,
pero el workflow conserva límites, routing, paralelismo, verificación y cierre. El nombre deja claro
que un `StateGraph` no es automáticamente un DeepAgent.
La lógica compartida que también usan agentes, como `OrderDesk`, permanece en `trading` y no se
duplica dentro del workflow.

### `runtime`

Ensamblará modelos, integraciones, MCP, contexto, checkpointer, perfiles, instrumentación y workflows.
Será el único punto que CLI, API, Studio y evals necesiten importar para construir el análisis de
producción. La actual instrumentación de `harness/runtime.py`, `observation.py`, `profile.py` y
`trace.py` se moverá aquí porque es parte de la ejecución normal.

### `optimization`

Contendrá exclusivamente el ciclo experimental `better-harness`: datasets, variantes, proponente,
evaluación de splits, aceptación y exportación. `optimization/README.md` comenzará con una nota
explícita: “opcional; no requerido para ejecutar fin-graph”. El TOML se moverá desde `harness/fin.toml`
a `optimization/fin.toml`.

La orquestación del proponente seguirá aquí, pero consumirá la fábrica explícita de
`agents/optimizer/proposer.py`; así su DeepAgent aparece en el catálogo sin hacer que producción
dependa del optimizador.

`optimization/fin.toml` será un archivo de desarrollo del checkout y no se incluirá como configuración
operativa del wheel. Los comandos del harness aceptarán una ruta explícita. Cuando no se proporcione,
solo usarán `optimization/fin.toml` si existe en el directorio del repositorio; una instalación desde
wheel mostrará un error claro solicitando `--config`, en lugar de fingir que esa configuración es
necesaria para la aplicación.

## Entradas y recorridos documentados

`agents/README.md` incluirá una tabla con cuatro recorridos:

1. Compatibilidad AI Hedge Fund → runtime compatible → supervisor DeepAgents → 19 personas →
   portfolio compatible.
2. App nativa → runtime de análisis → workflow LangGraph → analistas/herramientas → tesis verificadas
   → orden paper opcional.
3. Chat → supervisor DeepAgents → consultas, análisis y herramientas de orden con aprobación.
4. Solver verificado → supervisor DeepAgents asíncrono → hijos y verificadores dentro de cuotas.

Cada subpaquete tendrá un README breve con “entrada”, “estado”, “agentes”, “herramientas”, “salida” y
“pruebas”. `docs/arquitectura-agentes.md` se reemplazará por una arquitectura financiera actual, con
un diagrama Mermaid que enlace estos READMEs en lugar de repetir detalles.

## Compatibilidad y migración

- Se preservan `src.*`, `v2.*`, `app.backend.*`, comandos CLI, rutas HTTP, schemas y respuestas SSE.
- `langgraph.json` apuntará a `fin_graph.runtime.analysis`.
- Los imports internos `fin_graph.graphs.*` y `fin_graph.harness.*` se actualizarán de forma mecánica.
- Solo se conservarán shims para imports documentados o probados como superficies públicas. Cada shim
  tendrá una única importación y comentario de deprecación; no contendrá lógica.
- El paquete experimental `fin_graph.work` conservará contratos, servicio y persistencia. Su
  construcción DeepAgents, lectura de control en proceso y modelo offline se moverán a
  `agents/solver`; el shim público documentado de `work.graphs` lo consumirá como dependencia.
- Los movimientos se harán por grupos pequeños con commits separados para mantener revisiones
  legibles y permitir `git bisect`.

## Secuencia de migración

1. Añadir pruebas de arquitectura: catálogo resoluble, dirección de imports, ausencia de
   `create_deep_agent` fuera de `agents`, y `better_harness` ausente del grafo de dependencias de
   producción.
2. Crear `agents/common` y mover infraestructura compartida sin cambiar comportamiento.
3. Crear `agents/analysis`, mover personas y supervisor compatible, y conectar el catálogo.
4. Crear `agents/chat`, `agents/solver` y la fábrica opcional `agents/optimizer`.
5. Crear `workflows` y mover los grafos deterministas.
6. Crear `runtime` y cambiar API, CLI, Studio y evals a sus fábricas.
7. Crear `optimization/harness`, mover `fin.toml` y actualizar el CLI opcional.
8. Reescribir documentación obsoleta y añadir los comandos `agents list/describe`.
9. Eliminar paquetes vacíos y shims no públicos después de verificar todos los consumidores.

## Pruebas y criterios de aceptación

- La suite completa continúa sin fallos.
- Los 39 contratos de compatibilidad permanecen cerrados.
- `eval run --suite smoke --model scripted` conserva 3/3.
- La aceptación SQLite conserva 14/14 y el mismo `public_digest`.
- Las pruebas de importación del wheel siguen pasando desde un entorno extraído.
- `fin-graph agents list` enumera supervisores y las diecinueve personas sin duplicados.
- El proponente opcional aparece como agente de workspace y no se importa al arrancar producción.
- Cada descriptor resuelve una fábrica real.
- Los nodos deterministas se documentan como workflows y no aparecen como agentes en el catálogo.
- Un test estático falla si aparece `create_deep_agent` fuera de `fin_graph/agents`.
- Un test de dependencias falla si producción importa `fin_graph.optimization` o `better_harness`.
- `fin-graph app` y `fin-graph eval run` funcionan sin instalar el extra `harness`.
- `fin-graph harness validate optimization/fin.toml` funciona con el extra opcional; fuera de un
  checkout, omitir la ruta devuelve un error accionable.
- El wheel no contiene archivos de resultados, claves, bases de datos ni configuración privada.

## Decisiones dejadas al implementador

- Nombres internos de helpers privados durante los movimientos.
- Orden exacto de imports y detalles de formato.
- Si un README de subpaquete necesita un segundo diagrama pequeño además del diagrama principal.
- El texto preciso de los avisos de deprecación, siempre que indiquen la ruta nueva.

## Riesgos y mitigaciones

- **Imports circulares:** aplicar la regla de dependencia con pruebas de arquitectura antes de mover
  runtime y workflows.
- **Cambios accidentales de comportamiento:** separar movimientos de cualquier edición lógica y usar
  evals/aceptación después de cada grupo.
- **Catálogo divergente:** derivar personas desde la fuente de dominio y probar que las fábricas se
  resuelven.
- **Confundir optimización con producción otra vez:** impedir por test imports desde producción hacia
  `optimization` y documentar el extra opcional en su raíz.
- **Shims que perpetúan la estructura antigua:** conservar solo superficies públicas verificadas y
  registrar cada shim con fecha/ruta de eliminación futura.
