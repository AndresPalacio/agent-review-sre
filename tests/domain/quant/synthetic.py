"""Deterministic market data for the quant tests, identical to what produced ``reference.json``.

``bars`` is a seeded geometric random walk; the companies are statement lines newest first.
The reference values were computed by running ai-hedge-fund's original pandas analysts on
exactly these inputs, so the port is checked against the code it came from.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any

from fin_graph.domain.market import (
    CompanyNews,
    FinancialMetrics,
    InsiderTrade,
    LineItem,
    Price,
)

DATA = Path(__file__).parent / "data"


@cache
def reference() -> dict[str, Any]:
    return json.loads((DATA / "reference.json").read_text(encoding="utf-8"))


def raw_bars(
    n: int = 320, seed: int = 7, drift: float = 0.0004, vol: float = 0.018, start: float = 100.0
) -> list[dict[str, float]]:
    state = seed

    def rnd() -> float:
        nonlocal state
        state = (1103515245 * state + 12345) % (2**31)
        return state / 2**31

    out = []
    price = start
    for index in range(n):
        u1, u2 = max(rnd(), 1e-12), rnd()
        z = math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)
        open_ = price
        close = price * math.exp(drift + vol * z)
        high = max(open_, close) * (1 + 0.004 + 0.003 * rnd())
        low = min(open_, close) * (1 - 0.004 - 0.003 * rnd())
        volume = int(1_000_000 * (0.6 + rnd()))
        out.append(
            {
                "open": round(open_, 4),
                "high": round(high, 4),
                "low": round(low, 4),
                "close": round(close, 4),
                "volume": volume,
                "i": index,
            }
        )
        price = close
    return out


def bars(**kwargs: Any) -> list[Price]:
    """Daily bars from 2024-01-01, one per calendar day."""
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        Price(
            time=start + timedelta(days=int(bar["i"])),
            open=bar["open"],
            high=bar["high"],
            low=bar["low"],
            close=bar["close"],
            volume=int(bar["volume"]),
        )
        for bar in raw_bars(**kwargs)
    ]


#: The series ``reference.json`` names, with the arguments that generate them.
TECHNICAL_CASES: dict[str, dict[str, Any]] = {
    "walk": {},
    "up": {"drift": 0.004, "vol": 0.01},
    "down": {"drift": -0.004, "vol": 0.01},
    "short": {"n": 40},
}


def _periods(
    base: dict[str, float], n: int, growth: float, **overrides: list[float]
) -> list[dict[str, float]]:
    out = []
    for index in range(n):
        factor = (1 + growth) ** -index
        item = {
            key: value * factor if key != "outstanding_shares" else value
            for key, value in base.items()
        }
        item.update({key: values[index] for key, values in overrides.items()})
        out.append(item)
    return out


QUALITY = _periods(
    {
        "revenue": 380e9, "net_income": 95e9, "gross_profit": 170e9, "gross_margin": 0.45,
        "depreciation_and_amortization": 11e9, "capital_expenditure": 10e9,
        "free_cash_flow": 100e9, "working_capital": -2e9, "total_debt": 110e9,
        "cash_and_equivalents": 60e9, "interest_expense": 3e9, "operating_income": 115e9,
        "ebit": 115e9, "ebitda": 126e9, "total_assets": 350e9, "total_liabilities": 290e9,
        "current_assets": 140e9, "current_liabilities": 135e9, "shareholders_equity": 60e9,
        "outstanding_shares": 15.5e9, "earnings_per_share": 6.1, "book_value_per_share": 3.9,
        "dividends_and_other_cash_distributions": -15e9,
        "issuance_or_purchase_of_equity_shares": -80e9,
    },
    6,
    0.07,
    gross_margin=[0.46, 0.45, 0.44, 0.43, 0.42, 0.41],
)  # fmt: skip
DEEP_VALUE = _periods(
    {
        "revenue": 2.0e9, "net_income": 120e6, "gross_profit": 500e6, "gross_margin": 0.25,
        "depreciation_and_amortization": 40e6, "capital_expenditure": 35e6,
        "free_cash_flow": 110e6, "working_capital": 600e6, "total_debt": 100e6,
        "cash_and_equivalents": 400e6, "interest_expense": 5e6, "operating_income": 160e6,
        "ebit": 160e6, "ebitda": 200e6, "total_assets": 1.6e9, "total_liabilities": 500e6,
        "current_assets": 1.2e9, "current_liabilities": 450e6, "shareholders_equity": 1.1e9,
        "outstanding_shares": 100e6, "earnings_per_share": 1.2, "book_value_per_share": 11.0,
        "dividends_and_other_cash_distributions": -30e6,
        "issuance_or_purchase_of_equity_shares": -10e6,
    },
    6,
    0.02,
)  # fmt: skip
DISTRESSED = _periods(
    {
        "revenue": 900e6, "net_income": -80e6, "gross_profit": 120e6, "gross_margin": 0.13,
        "depreciation_and_amortization": 60e6, "capital_expenditure": 90e6,
        "free_cash_flow": -50e6, "working_capital": -150e6, "total_debt": 1.4e9,
        "cash_and_equivalents": 50e6, "interest_expense": 95e6, "operating_income": -20e6,
        "ebit": -20e6, "ebitda": 40e6, "total_assets": 1.8e9, "total_liabilities": 1.75e9,
        "current_assets": 300e6, "current_liabilities": 420e6, "shareholders_equity": 50e6,
        "outstanding_shares": 200e6, "earnings_per_share": -0.4, "book_value_per_share": 0.25,
        "dividends_and_other_cash_distributions": 0.0,
        "issuance_or_purchase_of_equity_shares": 120e6,
    },
    6,
    -0.05,
    earnings_per_share=[-0.4, -0.2, 0.1, 0.3, -0.1, 0.2],
)  # fmt: skip
#: name → (ticker, statement lines newest first, market cap).
COMPANIES: dict[str, tuple[str, list[dict[str, float]], float]] = {
    "quality": ("AAPL", QUALITY, 2.9e12),
    "deep_value": ("DEEP", DEEP_VALUE, 0.6e9),
    "distressed": ("DIST", DISTRESSED, 0.25e9),
}


def line_items(name: str) -> list[LineItem]:
    ticker, items, _ = COMPANIES[name]
    return [
        LineItem(ticker=ticker, report_period=date(2023 - index, 12, 31), period="annual", **item)
        for index, item in enumerate(items)
    ]


def _api(kind: str, ticker: str) -> Any:
    return json.loads((DATA / "api" / kind / f"{ticker}.json").read_text(encoding="utf-8"))


def metrics(ticker: str) -> list[FinancialMetrics]:
    """The recorded TTM metrics for AAPL, MSFT or TSLA, newest first."""
    return [
        FinancialMetrics(**item) for item in _api("financial_metrics", ticker)["financial_metrics"]
    ]


def company_metrics(name: str) -> list[FinancialMetrics]:
    """The metrics the reference used for a synthetic company."""
    ticker, _, market_cap = COMPANIES[name]
    if name == "quality":
        return metrics("AAPL")
    return [
        item.model_copy(update={"ticker": ticker, "market_cap": market_cap})
        for item in metrics("TSLA")
    ]


def insider_trades(ticker: str) -> list[InsiderTrade]:
    return [InsiderTrade(**item) for item in _api("insider_trades", ticker)["insider_trades"]]


def news(ticker: str, *, rotate_labels: bool = False) -> list[CompanyNews]:
    """Recorded headlines; ``rotate_labels`` assigns positive/negative/neutral in turn."""
    items = [CompanyNews(**item) for item in _api("news", ticker)["news"]]
    if not rotate_labels:
        return items
    labels = ("positive", "negative", "neutral")
    return [
        item.model_copy(update={"sentiment": labels[index % 3]}) for index, item in enumerate(items)
    ]


def prices(ticker: str) -> list[Price]:
    return [Price(**item) for item in _api("prices", ticker)["prices"]]
