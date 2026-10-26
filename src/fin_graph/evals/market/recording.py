"""Record what a market-data provider returns into the fixture layout the evals replay.

``record_scenario`` asks any :class:`MarketDataClient` (Yahoo by default) for everything the
analysts read, as of one date, and writes ``<dir>/<TICKER>/*.json`` plus a ``scenario.yml``.
The ``answer.yml`` is never generated: the expected thesis is a human judgement, written after
reading the recorded data.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from fin_graph.integrations.market_data import ANALYST_LINE_ITEMS, MarketDataClient

LOOKBACK_DAYS = 420


async def record_ticker(client: MarketDataClient, ticker: str, as_of: date) -> dict[str, Any]:
    """Every file one ticker needs, as JSON-ready values."""
    start = as_of - timedelta(days=LOOKBACK_DAYS)
    prices = await client.prices(ticker, start, as_of)
    metrics = await client.financial_metrics(ticker, as_of, limit=10)
    items = await client.line_items(ticker, ANALYST_LINE_ITEMS, as_of, limit=10)
    trades = await client.insider_trades(ticker, as_of, start=as_of - timedelta(days=365))
    news = await client.company_news(ticker, as_of, limit=100)
    cap = await client.market_cap(ticker, as_of)
    files: dict[str, Any] = {
        "prices.json": {"prices": [item.model_dump(mode="json") for item in prices]},
        "financial_metrics.json": {
            "financial_metrics": [
                item.model_dump(mode="json", exclude_none=True) for item in metrics
            ]
        },
        "line_items.json": {"search_results": [item.model_dump(mode="json") for item in items]},
        "insider_trades.json": {
            "insider_trades": [item.model_dump(mode="json", exclude_none=True) for item in trades]
        },
        "news.json": {"news": [item.model_dump(mode="json", exclude_none=True) for item in news]},
    }
    if cap is not None:
        files["market_cap.json"] = cap
    return files


async def record_scenario(
    client: MarketDataClient,
    tickers: Sequence[str],
    as_of: date,
    directory: Path,
    *,
    description: str = "",
    horizon: str = "medium",
) -> list[Path]:
    """Write the recorded fixtures and the scenario file; returns the files written."""
    written: list[Path] = []
    for ticker in tickers:
        for name, content in (await record_ticker(client, ticker, as_of)).items():
            path = directory / ticker / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(content, indent=1, default=str) + "\n", encoding="utf-8")
            written.append(path)
    scenario = directory / "scenario.yml"
    scenario.write_text(
        yaml.safe_dump(
            {
                "scenario_id": directory.name,
                "description": description or f"Recorded from {client.provider} as of {as_of}.",
                "tickers": list(tickers),
                "as_of": as_of.isoformat(),
                "horizon": horizon,
                "question": "",
                "scenario_difficulty": 2,
                "adversarial_signals": [],
                "synthetic": False,
                "source": client.provider,
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    written.append(scenario)
    return written
