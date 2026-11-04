# Operación de la app local

## Arranque

```powershell
Copy-Item .env.example .env
uv sync --extra app
uv run fin-graph app --host 127.0.0.1 --port 8000
```

Abre `http://127.0.0.1:8000`. El directorio se elige con `--data-dir` o
`FIN_GRAPH_DATA_DIR`; contiene SQLite, checkpoints, artefactos y una clave nativa local. Haz backup
de ese directorio con la aplicación detenida.

La instalación base omite TensorFlow. Solo si necesitas los modelos LSTM, instala el extra ML:

```powershell
uv sync --extra app --extra ml
```

La imagen Docker también es ligera por defecto. Construye la variante ML explícitamente con
`docker build --build-arg FIN_GRAPH_INSTALL_ML=true -t fin-graph:ml .`.

## Ollama

Instala Ollama, inicia su servicio y descarga `llama3.2:3b`, o usa la pantalla Modelos. Verifica:

```powershell
Invoke-RestMethod http://localhost:11434/api/tags
uv run fin-graph models
```

Sin un modelo disponible siguen funcionando los cálculos deterministas, catálogos, CRUD de flujos
y backtests algorítmicos; chat y texto libre pueden aparecer degradados.

## Datos y cartera

Yahoo está activo sin clave. Para fixtures configura `MARKET_DATA_PROVIDER=fixtures` y
`MARKET_FIXTURES_DIR`. La cartera es paper-only y el ledger persiste en el directorio de datos.

```powershell
uv run fin-graph analyze --tickers AAPL,MSFT
uv run fin-graph portfolio --json
uv run fin-graph orders --help
```

## Diagnóstico

- `GET /health` comprueba proceso; `GET /v1/ready` comprueba dependencias.
- `uv run fin-graph integrations list` muestra proveedores configurados.
- `uv run fin-graph harness runtime` muestra presupuesto y huella del grafo.
- Los secretos no se escriben en logs, checkpoints, DOM, localStorage ni capturas E2E.

Para detener usa Ctrl+C. La app local no debe exponerse directamente a Internet.
