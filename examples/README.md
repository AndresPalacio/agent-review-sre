# Ejemplos financieros multiagente

Estos ejemplos muestran el flujo asíncrono real de Fin Graph con datos financieros
sintéticos. No consultan cuentas, mercados ni carteras reales.

## Supervisor y agentes DeepAgents

Desde la raíz del repositorio:

```powershell
$env:UV_NO_SYNC='1'
$env:PYTHONPATH=(Join-Path (Resolve-Path '.').Path 'src')
.\.venv\Scripts\python.exe examples/demo_supervisor_async.py --scenario lifecycle
```

El supervisor crea dos hijos durables y acotados:

```text
pedido sobre AAPL
        |
        v
supervisor asíncrono
   |             |
   v             v
fundamentals   technicals
   |             |
   +------> síntesis
```

- `fundamentals` lee ratios fundamentales sintéticos.
- `technicals` lee indicadores técnicos sintéticos.
- `AsyncSubAgentMiddleware` crea, actualiza y consulta ambos trabajos.
- `WorkService` conserva identidad, revisión, cancelación e idempotencia.
- `PilotGuard` limita herramientas, hijos, plazo y revisión.

La demostración actualiza el horizonte del agente técnico mientras la ejecución está
activa, reconstruye el gateway con el mismo ledger y verifica que el trabajo se reanuda
sin duplicarse. Para probar cancelación:

```powershell
.\.venv\Scripts\python.exe examples/demo_supervisor_async.py --scenario cancel
```

## Panel HTTP y MCP

Inicia el panel local:

```powershell
.\.venv\Scripts\python.exe examples/demo_supervisor_async.py --serve
```

Abre <http://127.0.0.1:8012>. El panel, la API HTTP y MCP usan el mismo `WorkService`.
En otra terminal puedes probar la misma ejecución por ambos transportes:

```powershell
.\.venv\Scripts\python.exe examples/demo_mcp_async.py
```

El cliente MCP inicia un análisis de MSFT, repite el comando por HTTP para comprobar
idempotencia, actualiza `technicals` y finalmente cancela el trabajo.

## Evals del solver

El benchmark de `packages/workflow` ejecuta pares comparables: un agente LangChain base
y el solver DeepAgents. El evaluador inspecciona los documentos resultantes; no acepta
como evidencia la afirmación del modelo.

```powershell
Set-Location packages/workflow
$env:UV_NO_SYNC='1'
$env:PYTHONPATH=(Join-Path (Resolve-Path '.').Path 'src')+';'+(Resolve-Path '.').Path
..\..\.venv\Scripts\python.exe examples/solver_benchmark.py --case research-holdout
```

La familia `research` descubre un manifiesto financiero, lee universo, fundamentales y
política de selección, y escribe `artifacts/watchlist.json`. También hay escenarios de
datos faltantes, fallos de herramienta, recuperación, presupuesto agotado y holdouts.
El reporte separa resultados por brazo y muestra llamadas, bloqueos, recuperaciones,
coste observable y verificación independiente.

Para ejecutar las pruebas del benchmark:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/test_solver_evaluation.py -q
```

El modo predeterminado es determinista y no usa credenciales. Un proveedor real solo se
activa de forma explícita mediante `--model-factory modulo:crear_modelo`.
