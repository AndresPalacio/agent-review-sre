# Configuración opcional de optimización

`fin.toml` configura evaluaciones y superficies editables para desarrollo. No se incluye como
configuración operativa del wheel y no es necesario para usar la aplicación.

```powershell
uv sync --extra harness
uv run --extra harness fin-graph harness validate optimization/fin.toml
uv run --extra harness fin-graph harness smoke optimization/fin.toml
```

Fuera del checkout pasa una ruta explícita con `--config`.
