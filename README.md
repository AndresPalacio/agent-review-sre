# fin-graph — AI Hedge Fund compatible

Reemplazo limpio y autónomo de AI Hedge Fund, construido con LangGraph, DeepAgents y modelos
cuantitativos tipados. Conserva los contratos HTTP, Python y CLI de la aplicación de referencia,
añade una interfaz web integrada y ofrece una API nativa con autenticación, aprobaciones y
persistencia durable.

El producto analiza acciones, ejecuta backtests point-in-time, compone flujos de analistas y
administra una cartera de paper trading. Es software educativo: **no ejecuta operaciones reales**
ni constituye asesoría financiera.

## Qué incluye

- Los 19 analistas/personas de AI Hedge Fund y sus identificadores históricos.
- `run_hedge_fund`, `Backtester`, modelos, utilidades y rutas de importación compatibles.
- API `/api/*` compatible para conectar el frontend o un cliente existente sin cambiarlo.
- API `/v1/*` nativa para análisis, chat, órdenes paper, aprobaciones, scheduler y auditoría.
- Flujos visuales, historial, streaming SSE, backtests compatibles y algorítmicos.
- Yahoo Finance, Financial Datasets, fixtures offline y servidores MCP declarados.
- Ollama local y proveedores cloud configurables; las claves de la UI permanecen en memoria.
- SQLite para una instancia local y Postgres para despliegue distribuido.

La referencia `ai-hedge-fund` no se importa ni se necesita en ejecución. Las pruebas diferenciales
usan snapshots congelados y recorridos de navegador para demostrar la compatibilidad.

## Requisitos

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Node.js 22.12+ y npm solo para desarrollar la interfaz
- Docker Desktop opcional para contenedores/Postgres
- Ollama opcional para inferencia local

## Inicio rápido local

```powershell
Copy-Item .env.example .env
uv sync --extra app
uv run fin-graph app
```

Abre `http://127.0.0.1:8000`. El comando crea almacenamiento local dentro del directorio de datos,
sirve la UI compilada y habilita la API compatible únicamente para uso local.

Con Docker:

```powershell
docker compose up --build app
```

La configuración completa está en [operación local](docs/operations/local-app.md) y
[matriz de configuración](docs/compatibility/configuration-matrix.md).

## Interfaz web

La UI ofrece:

- biblioteca y editor de flujos;
- canvas de analistas y conexiones;
- ejecución en vivo, razonamiento, terminal, reportes e historial;
- backtests, cancelación y resultados;
- selección de modelos cloud/Ollama y estado de descargas;
- cartera paper, órdenes, aprobaciones y auditoría.

Para desarrollo:

```powershell
uv run fin-graph app
Set-Location web
npm ci
npm run dev
```

Vite reenvía la API al puerto 8000. No pongas secretos en variables `VITE_*`, URLs ni localStorage.

## CLI compatible

```powershell
uv run fin-graph run --tickers AAPL,MSFT --analysts-all --show-reasoning
uv run fin-graph backtest --tickers AAPL,MSFT --start-date 2024-01-01 --end-date 2024-06-30
uv run fin-graph models
uv run fin-graph algorithms
uv run fin-graph event-study --tickers AAPL --as-of 2024-06-30 --price-start 2023-01-01
```

También existe la superficie nativa:

```powershell
uv run fin-graph analyze --tickers AAPL,MSFT --propose
uv run fin-graph portfolio --json
uv run fin-graph orders --help
uv run fin-graph chat
uv run fin-graph eval run --suite smoke --model scripted
uv run fin-graph harness runtime
uv run fin-graph agents list
uv run fin-graph agents describe analysis.persona_team
```

`--propose` crea órdenes paper pendientes; una aprobación humana y los límites deterministas siguen
siendo obligatorios antes del fill simulado.

## Arquitectura multiagente

El código separa agentes DeepAgents, workflows LangGraph, runtime productivo, trading determinista
y optimización opcional. `fin-graph agents list` muestra el catálogo ejecutable —incluidas las 19
personas financieras— sin construir modelos ni hacer llamadas externas. Consulta la
[guía de arquitectura](docs/arquitectura-agentes.md) y los
[README colocados junto a los agentes](src/fin_graph/agents/README.md).

better-harness no es necesario para usar la aplicación. Vive bajo `fin_graph.optimization` y solo
se carga al invocar los comandos opcionales documentados en
[optimización](docs/better-harness.md); su configuración de desarrollo está en
`optimization/fin.toml`.

## API y conexión de clientes existentes

El servidor local conserva las rutas y JSON esperados por AI Hedge Fund, entre ellas:

- `/api/hedge-fund/agents`, `/api/hedge-fund/run`, `/api/hedge-fund/backtest`;
- `/api/hedge-fund/flows` y sus runs;
- `/api/backtest`, `/api/algorithms`, `/api/models`, `/api/ollama`;
- `/api/api-keys`, almacenadas cifradas cuando se usa Postgres.

El cliente puede mantener su URL base en `http://localhost:8000`. Los IDs públicos se limitan al
rango entero seguro de JavaScript y los streams usan el mismo formato SSE. Consulta el
[manifiesto de API](docs/compatibility/api-manifest.md) y la
[guía de compatibilidad](docs/compatibility/ai-hedge-fund.md).

La API compatible replica el perfil local sin autenticación de la referencia. Por seguridad solo
puede enlazarse a loopback salvo habilitación explícita. Para acceso remoto usa la API nativa con
`X-API-Key`, TLS y Postgres; consulta [despliegue remoto](docs/operations/remote-deployment.md).

## API nativa autenticada

```powershell
$env:DATABASE_URL = "postgresql+psycopg://fin_graph:fin_graph@localhost:5433/fin_graph"
$env:BOOTSTRAP_ADMIN_KEY = "cambie-esta-clave-por-32-caracteres-minimo"
uv run fin-graph serve
```

Rutas principales: `/v1/analyses`, `/v1/chat`, `/v1/orders`, `/v1/portfolio`,
`/v1/schedules`, `/v1/integrations`, `/v1/api-keys`, `/v1/health` y `/v1/ready`.

Los roles son `viewer`, `operator`, `approver` y `admin`. Las claves se muestran una sola vez y se
guardan como hash; las credenciales de proveedor nunca forman parte de checkpoints o respuestas.

## Modelos y datos

`.env.example` usa Ollama con `llama3.2:3b`. Para arrancar Ollama y descargar modelos también se
puede usar la pantalla Modelos. Para un proveedor cloud configura `LLM_PROVIDER`, su clave oficial
y opcionalmente `LLM_MODEL_REASONING`, `LLM_MODEL_WORKER`, `LLM_MODEL_FAST` y `LLM_MODEL_JUDGE`.

Los resultados cuantitativos, tamaños, límites y backtests deterministas son reproducibles con las
mismas entradas. El texto generado por un LLM vivo puede variar entre ejecuciones y proveedores.
Yahoo no requiere clave; Financial Datasets necesita `FINANCIAL_DATASETS_API_KEY`.

## Python compatible

Las importaciones históricas se resuelven dentro del wheel:

```python
from src.main import run_hedge_fund
from src.backtester import Backtester
from app.backend.main import app as compatible_fastapi_app

assert callable(run_hedge_fund)
assert compatible_fastapi_app.title
```

La API moderna vive en `fin_graph`. El paquete independiente
[bm-workflow](packages/workflow/README.md) se conserva para workflows tipados y versionados.

## Seguridad financiera

- Solo paper trading; no hay adaptador de broker real.
- Kill switch, máximo por orden, máximo por posición y límite diario son deterministas.
- Short selling está deshabilitado por defecto.
- Las mutaciones requieren aprobación salvo allowlist explícita en un entorno no productivo.
- Los webhooks se autentican por token o HMAC.
- El servidor remoto exige TLS en el proxy y claves nativas por rol.

## Desarrollo y verificación

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run lint-imports
uv run --extra app pytest
uv run --project packages/workflow pytest packages/workflow/tests
Set-Location web
npm run typecheck
npm test -- --run
npm run build
npm run test:e2e
```

Construcción:

```powershell
uv lock --check
uv build --wheel
docker build -t fin-graph:compat .
```

## Licencias

El proyecto se distribuye bajo Apache-2.0. [NOTICE](NOTICE) conserva las atribuciones de AI Hedge
Fund, Vibe-Trading, LangGraph Starter Kit y cualquier material adaptado que permanece distribuido.
