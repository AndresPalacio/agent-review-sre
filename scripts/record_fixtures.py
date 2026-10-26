"""Record live market data into an equity evaluation scenario (replaces port_evals.py).

    uv run python scripts/record_fixtures.py --tickers NVDA,AMD --as-of 2026-10-23 \\
        --scenario 016-nvda-amd-2026-10

Reads Yahoo by default (no key), or Financial Datasets with ``--provider financial_datasets``
when ``FINANCIAL_DATASETS_API_KEY`` is set. Write ``answer.yml`` by hand after reading the data:
the expected thesis is a judgement, never a recording.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date
from pathlib import Path

from fin_graph.evals.market.recording import record_scenario
from fin_graph.evals.market.scenarios import DATA_ROOT
from fin_graph.integrations.financial_datasets.client import FinancialDatasetsClient
from fin_graph.integrations.market_data import MarketDataClient
from fin_graph.integrations.yahoo.client import YahooClient


def _client(provider: str) -> MarketDataClient:
    if provider == "yahoo":
        return YahooClient()
    key = os.environ.get("FINANCIAL_DATASETS_API_KEY")
    if not key:
        raise SystemExit("FINANCIAL_DATASETS_API_KEY is not set")
    return FinancialDatasetsClient(key)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickers", required=True, help="Comma-separated tickers.")
    parser.add_argument("--as-of", required=True, type=date.fromisoformat)
    parser.add_argument("--scenario", required=True, help="Folder name, e.g. 016-nvda-2026-10.")
    parser.add_argument("--provider", choices=("yahoo", "financial_datasets"), default="yahoo")
    parser.add_argument("--horizon", choices=("short", "medium", "long"), default="medium")
    parser.add_argument("--root", type=Path, default=DATA_ROOT)
    args = parser.parse_args()
    tickers = [item.strip().upper() for item in args.tickers.split(",") if item.strip()]
    written = asyncio.run(
        record_scenario(
            _client(args.provider),
            tickers,
            args.as_of,
            args.root / args.scenario,
            horizon=args.horizon,
        )
    )
    for path in written:
        print(path)
    print("Now write answer.yml for", args.scenario)
    return 0


if __name__ == "__main__":
    sys.exit(main())
