# Compatibilidad con AI Hedge Fund

fin-graph reemplaza la aplicación de referencia sin importar su código ni acceder a su base de
datos. Un cliente existente puede conservar `http://localhost:8000` como URL base y continuar
usando los mismos cuerpos, respuestas, nombres de analistas, flujos y eventos SSE.

## Superficies equivalentes

| Referencia | Reemplazo |
|---|---|
| `POST /hedge-fund/run` | Mismo JSON de solicitud y stream de progreso/reporte |
| `POST /hedge-fund/backtest` | Backtest compatible point-in-time |
| `/flows/` | CRUD, duplicado, runs, ciclos, cancelación e historial |
| `/language-models/` | Catálogo cloud y proveedores |
| `/ollama/*` | Estado, inicio/parada y administración de modelos |
| `/api-keys/*` | Claves de proveedores, cifradas en almacenamiento durable |
| `src.main.run_hedge_fund` | Shim Python hacia el runtime nativo |
| `src.backtester.Backtester` | Backtester compatible |
| CLI histórica | `fin-graph run`, `backtest`, `models`, `algorithms` |

Los 19 IDs de analista y sus resultados normalizados están congelados por pruebas de contrato. Los
IDs numéricos de flujos y claves se mantienen dentro del rango entero seguro de JavaScript.

## Conectar el frontend original

No se modifica su fuente. Inicia el reemplazo:

```powershell
uv run fin-graph app
```

El valor predeterminado del frontend original, `VITE_API_URL=http://localhost:8000`, funciona de
forma directa. CORS solo debe configurarse cuando se sirve desde otro origen. El recorrido de
navegador crea un flujo, lo abre y lo ejecuta contra fin-graph como parte de la suite E2E.

## Determinismo y modelos vivos

La forma de los JSON, señales cuantitativas, sizing, restricciones y fixtures offline son
deterministas. La redacción producida por Ollama o un proveedor cloud puede variar; la equivalencia
se verifica mediante reglas semánticas y estructurales, no comparando texto libre byte por byte.

## Diferencias intencionales seguras

- Toda operación de cartera es paper trading.
- Las credenciales no se devuelven después de guardarse; la UI solo mantiene su valor en memoria.
- La API compatible sin autenticación se limita a loopback de forma predeterminada.
- La API nativa ofrece roles, aprobaciones y auditoría adicionales.
- El reemplazo añade backtests algorítmicos `/v2`, event studies y modelos cuantitativos versionados.

No existe dependencia de ejecución respecto al checkout de referencia. Los smokes de proveedores
reales son optativos y no sustituyen las pruebas offline.
