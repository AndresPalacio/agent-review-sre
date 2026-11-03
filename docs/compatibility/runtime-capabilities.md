# Runtime capability matrix

The replacement exposes the same financial HTTP, CLI, flow, backtest, and paper-trading
contracts in both supported persistence modes. The acceptance runner executes the same
deterministic fixture against each mode and compares its public digest.

| Capability | SQLite local app | PostgreSQL distributed server |
|---|---:|---:|
| AI Hedge Fund compatibility API and web UI | Yes | Yes |
| Flow revisions, runs, cycles, and cancellation | Yes | Yes |
| v1 streamed analysis/backtest contract | Yes | Yes |
| v2 durable strategy-backtest jobs and artifacts | Yes | Yes |
| Encrypted provider-key metadata | Yes | Yes |
| Durable paper orders and portfolio | Yes | Yes |
| Restart persistence and optimistic compare-and-swap | Yes | Yes |
| Cross-process advisory locks | No | Yes |
| Supported multi-worker deployment | No | Yes |

SQLite is the zero-configuration single-process desktop profile. PostgreSQL is the server
profile for multiple workers and replicas. These are deployment capability differences, not
financial-result differences.

Run the local gate:

```powershell
uv run python scripts/acceptance_ai_hedge_fund.py --backend sqlite --output artifacts/acceptance
```

Run the distributed gate against an isolated PostgreSQL database:

```powershell
$env:FIN_GRAPH_TEST_DATABASE_URL = "postgresql+psycopg://..."
uv run python scripts/acceptance_ai_hedge_fund.py --backend postgres --output artifacts/acceptance
```

The generated JSON and Markdown contain statuses, capability flags, and a semantic SHA-256
digest. They never contain provider secrets, bootstrap keys, database URLs, or raw credentials.
