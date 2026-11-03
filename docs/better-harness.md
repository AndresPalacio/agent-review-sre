# Opcional: optimización con better-harness

La aplicación, API, CLI, agentes y evals normales no requieren better-harness ni
`optimization/fin.toml`. Esta integración se usa únicamente para experimentar con variantes
acotadas del análisis financiero y revisar evidencia antes de adoptar un cambio.

## Organización

- `fin_graph.runtime`: ensamblaje productivo, sin dependencias del optimizador.
- `fin_graph.optimization.harness`: dataset, runner, proposer, worker y artefactos opcionales.
- `fin_graph.agents.optimizer`: única fábrica DeepAgents del proposer.
- `optimization/fin.toml`: configuración del checkout; no se incluye en el wheel.

La separación está protegida por pruebas AST, contratos de import-linter y una prueba de wheel que
bloquea cualquier intento de importar `better_harness` al cargar CLI, servidor o evals normales.

## Instalar y validar

```powershell
uv sync --locked --extra harness
uv run --extra harness fin-graph harness validate optimization/fin.toml
uv run --extra harness fin-graph harness runtime
```

Dentro del checkout se permite omitir la ruta porque existe `optimization/fin.toml`. En una
instalación o directorio distinto hay que indicarla explícitamente:

```powershell
uv run --extra harness fin-graph harness validate --config C:\ruta\fin.toml
```

Dar la ruta posicional y `--config` a la vez es un error. El CLI resuelve la configuración antes de
importar módulos opcionales, por lo que un uso normal de `fin-graph` no carga better-harness.

## Smoke reproducible

```powershell
uv run --extra harness fin-graph harness smoke optimization/fin.toml `
  --output-dir .harness-runs/smoke
```

El smoke usa fixtures y modelo scripted. Comprueba wiring, contratos, trazas y artefactos, pero no
demuestra calidad ni generalización de un modelo vivo.

## Baseline y optimización real

```powershell
# Baseline sin propuestas
uv run --env-file .env --extra harness fin-graph harness run optimization/fin.toml `
  --max-iterations 0 --repeat 3 --output-dir .harness-runs/baseline

# Una campaña con proposer configurable
uv run --env-file .env --extra harness fin-graph harness run optimization/fin.toml `
  --model ollama:llama3.2:3b --proposer-model ollama:llama3.2:3b `
  --max-iterations 2 --repeat 3 --output-dir .harness-runs/optimization
```

El runner mide train y holdout en procesos separados. Un candidato solo avanza si mejora train sin
perder casos holdout ya aprobados. Scorecard se reserva para baseline y variante final. Ninguna
variante se copia al producto automáticamente.

## Superficies editables

`optimization/fin.toml` permite únicamente:

- prompts de plan, analista, síntesis, narrativa, juez y cartera;
- selección/descripción de herramientas conservando schemas y metadatos;
- skill, refinamiento de plan y middleware de estrategia.

Quedan fuera: seguridad, límites de trading, adaptadores, persistencia, evaluadores, corpus,
particiones y código de escritura. `surface_manifest.json` y validaciones AST cierran el conjunto.

## Evals y contratos

Los 16 escenarios financieros se dividen en train, holdout y scorecard. Cada ejecución conserva
invocaciones, argumentos redactados, orden parcial, evidencia, tesis, decisiones, tokens y latencia.
Los contratos comprueban herramientas obligatorias, propietario, precedencias, citas, stance,
categoría y acción esperada.

```powershell
uv run --extra harness fin-graph harness contract equities/012-two-tickers
```

Los timeouts son límites por caso, proposer y split. Un fallo o timeout se registra y no puede
contarse como aprobación.

## Inspección y exportación

```powershell
uv run --extra harness fin-graph harness inspect .harness-runs/optimization
uv run --extra harness fin-graph harness export .harness-runs/optimization `
  --output-dir .harness-runs/review
```

`export` genera archivos candidatos, diff y manifiesto para revisión; no modifica producción. Los
resultados, bases de datos, secretos y `optimization/fin.toml` no se empaquetan en el wheel.

## Límite de seguridad

El workspace y los subprocess aíslan variantes del checkout activo, pero no son un sandbox contra
Python hostil: conservan los permisos del usuario. Para código no confiable usa una VM, contenedor o
cuenta restringida sin secretos y con red limitada.
