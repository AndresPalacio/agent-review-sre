"""Read-only tools for the analysts: raw market data and deterministic scores.

Every tool resolves the configured data provider from the run context, so the model never picks
a provider and Yahoo, Financial Datasets or fixtures answer the same calls. Dates are
``YYYY-MM-DD`` and never later than today: the analysis is point in time.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool

from fin_graph.domain.requests.model import RequestParseError, normalize_ticker
from fin_graph.domain.tools import Domain, EvidenceType, ToolMeta
from fin_graph.integrations.analytics.engine import (
    DEFAULT_LOOKBACK_DAYS,
    AnalyticsEngine,
    summarize,
)
from fin_graph.integrations.base import IntegrationError
from fin_graph.integrations.context import RunContext, require_client
from fin_graph.integrations.market_data import MarketDataClient
from fin_graph.integrations.payload import tool_payload
from fin_graph.integrations.tooling import integration_tool

INTEGRATION = "analytics"
_VALUE_ANALYSTS = frozenset({Domain.VALUATION, Domain.BUFFETT, Domain.GRAHAM})
#: Evidence each compute tool produces; claims about it should cite that kind.
COMPUTE_EVIDENCE = {
    Domain.FUNDAMENTALS: EvidenceType.FINANCIALS,
    Domain.VALUATION: EvidenceType.VALUATION_MODEL,
    Domain.TECHNICALS: EvidenceType.INDICATORS,
    Domain.SENTIMENT: EvidenceType.NEWS,
    Domain.BUFFETT: EvidenceType.VALUATION_MODEL,
    Domain.GRAHAM: EvidenceType.VALUATION_MODEL,
}


def _meta(domains: set[Domain], kind: EvidenceType) -> ToolMeta:
    return ToolMeta(integration=INTEGRATION, domains=frozenset(domains), evidence_type=kind)


def _engine(runtime: ToolRuntime[RunContext]) -> AnalyticsEngine:
    return require_client(runtime, INTEGRATION, AnalyticsEngine)


def _data(runtime: ToolRuntime[RunContext], engine: AnalyticsEngine) -> MarketDataClient:
    client = require_client(runtime, engine.data_integration, object)
    if not isinstance(client, MarketDataClient):
        raise TypeError(f"{engine.data_integration} client is not a market data client")
    return client


def _ticker(raw: str) -> str:
    try:
        return normalize_ticker(raw)
    except RequestParseError as exc:
        raise IntegrationError(str(exc)) from exc


def parse_day(raw: str | None, *, field: str = "as_of") -> date:
    """``YYYY-MM-DD`` (empty = today), never after today."""
    today = datetime.now(UTC).date()
    if not raw:
        return today
    try:
        day = date.fromisoformat(raw.strip()[:10])
    except ValueError as exc:
        raise IntegrationError(f"{field} must be a YYYY-MM-DD date, got {raw!r}") from exc
    return min(day, today)


def _bounded(limit: int, maximum: int) -> int:
    return max(1, min(limit, maximum))


@integration_tool(_meta({Domain.TECHNICALS}, EvidenceType.PRICES))
async def get_prices(
    ticker: str, start: str, end: str, runtime: ToolRuntime[RunContext]
) -> tuple[str, Any]:
    """Daily prices for a ticker between two dates, with a summary of the move.

    Args:
        ticker: Ticker symbol, for example AAPL.
        start: First day, YYYY-MM-DD.
        end: Last day, YYYY-MM-DD (not after the analysis date).
    """
    engine = _engine(runtime)
    symbol = _ticker(ticker)
    first, last = parse_day(start, field="start"), parse_day(end, field="end")
    bars = await _data(runtime, engine).prices(symbol, first, last)
    rows = [bar.model_dump(mode="json") for bar in bars]
    if not bars:
        return tool_payload({"ticker": symbol, "bars": 0, "note": "no prices in range"}), rows
    closes = [bar.close for bar in bars]
    summary = {
        "ticker": symbol,
        "bars": len(bars),
        "first": {"day": bars[0].day.isoformat(), "close": closes[0]},
        "last": {"day": bars[-1].day.isoformat(), "close": closes[-1]},
        "return": round(closes[-1] / closes[0] - 1, 4) if closes[0] else None,
        "high": max(bar.high for bar in bars),
        "low": min(bar.low for bar in bars),
        "recent": rows[-10:],
    }
    return tool_payload(summary), rows


@integration_tool(_meta({Domain.FUNDAMENTALS, *_VALUE_ANALYSTS}, EvidenceType.FINANCIALS))
async def get_financial_metrics(
    ticker: str,
    runtime: ToolRuntime[RunContext],
    as_of: str = "",
    period: str = "ttm",
    limit: int = 5,
) -> tuple[str, Any]:
    """Valuation, profitability, growth and balance-sheet ratios known on a date, newest first.

    Args:
        ticker: Ticker symbol, for example AAPL.
        as_of: Analysis date YYYY-MM-DD; empty means today.
        period: ttm, annual or quarterly.
        limit: How many periods, 1 to 10.
    """
    engine = _engine(runtime)
    rows = await _data(runtime, engine).financial_metrics(
        _ticker(ticker), parse_day(as_of), period=period, limit=_bounded(limit, 10)
    )
    data = [row.model_dump(mode="json", exclude_none=True) for row in rows]
    return tool_payload(data), data


@integration_tool(_meta(set(_VALUE_ANALYSTS), EvidenceType.FINANCIALS))
async def search_line_items(
    ticker: str,
    line_items: list[str],
    runtime: ToolRuntime[RunContext],
    as_of: str = "",
    period: str = "annual",
    limit: int = 5,
) -> tuple[str, Any]:
    """Statement lines (revenue, net_income, free_cash_flow, total_debt...) per period.

    Args:
        ticker: Ticker symbol, for example AAPL.
        line_items: Line names, for example ["revenue", "net_income", "free_cash_flow"].
        as_of: Analysis date YYYY-MM-DD; empty means today.
        period: annual or ttm.
        limit: How many periods, 1 to 10.
    """
    engine = _engine(runtime)
    names = [name.strip() for name in line_items if name.strip()][:30]
    if not names:
        raise IntegrationError("line_items must name at least one line")
    rows = await _data(runtime, engine).line_items(
        _ticker(ticker), names, parse_day(as_of), period=period, limit=_bounded(limit, 10)
    )
    data = [row.model_dump(mode="json") for row in rows]
    return tool_payload(data), data


@integration_tool(_meta({Domain.SENTIMENT}, EvidenceType.NEWS))
async def get_company_news(
    ticker: str, runtime: ToolRuntime[RunContext], as_of: str = "", limit: int = 20
) -> tuple[str, Any]:
    """Recent headlines about a company published on or before a date, newest first.

    Args:
        ticker: Ticker symbol, for example AAPL.
        as_of: Analysis date YYYY-MM-DD; empty means today.
        limit: How many headlines, 1 to 100.
    """
    engine = _engine(runtime)
    rows = await _data(runtime, engine).company_news(
        _ticker(ticker), parse_day(as_of), limit=_bounded(limit, 100)
    )
    data = [row.model_dump(mode="json", exclude_none=True) for row in rows]
    return tool_payload(data), data


@integration_tool(_meta({Domain.SENTIMENT}, EvidenceType.INSIDER))
async def get_insider_trades(
    ticker: str, runtime: ToolRuntime[RunContext], as_of: str = "", limit: int = 50
) -> tuple[str, Any]:
    """Insider purchases and sales filed on or before a date; negative shares are sales.

    Args:
        ticker: Ticker symbol, for example AAPL.
        as_of: Analysis date YYYY-MM-DD; empty means today.
        limit: How many filings, 1 to 200.
    """
    engine = _engine(runtime)
    rows = await _data(runtime, engine).insider_trades(
        _ticker(ticker), parse_day(as_of), limit=_bounded(limit, 200)
    )
    data = [row.model_dump(mode="json", exclude_none=True) for row in rows]
    sold = sum(-(row.transaction_shares or 0) for row in rows if (row.transaction_shares or 0) < 0)
    bought = sum(row.transaction_shares or 0 for row in rows if (row.transaction_shares or 0) > 0)
    summary = {"filings": len(rows), "shares_bought": bought, "shares_sold": sold, "trades": data}
    return tool_payload(summary), data


@integration_tool(_meta(set(_VALUE_ANALYSTS), EvidenceType.FINANCIALS))
async def get_market_cap(
    ticker: str, runtime: ToolRuntime[RunContext], as_of: str = ""
) -> tuple[str, Any]:
    """Market capitalisation of a company on a date.

    Args:
        ticker: Ticker symbol, for example AAPL.
        as_of: Analysis date YYYY-MM-DD; empty means today.
    """
    engine = _engine(runtime)
    symbol, day = _ticker(ticker), parse_day(as_of)
    value = await _data(runtime, engine).market_cap(symbol, day)
    data = {"ticker": symbol, "as_of": day.isoformat(), "market_cap": value}
    return tool_payload(data), data


async def _compute(
    analyst: Domain, ticker: str, as_of: str, runtime: ToolRuntime[RunContext], **options: int
) -> tuple[str, Any]:
    engine = _engine(runtime)
    score = await engine.score(
        analyst, _ticker(ticker), parse_day(as_of), _data(runtime, engine), **options
    )
    return tool_payload(summarize(score)), score.model_dump(mode="json")


@integration_tool(_meta({Domain.FUNDAMENTALS}, COMPUTE_EVIDENCE[Domain.FUNDAMENTALS]))
async def compute_fundamentals(
    ticker: str, runtime: ToolRuntime[RunContext], as_of: str = ""
) -> tuple[str, Any]:
    """Score profitability, growth, financial health and price ratios; returns the signal.

    Args:
        ticker: Ticker symbol, for example AAPL.
        as_of: Analysis date YYYY-MM-DD; empty means today.
    """
    return await _compute(Domain.FUNDAMENTALS, ticker, as_of, runtime)


@integration_tool(_meta({Domain.VALUATION}, COMPUTE_EVIDENCE[Domain.VALUATION]))
async def compute_valuation(
    ticker: str, runtime: ToolRuntime[RunContext], as_of: str = ""
) -> tuple[str, Any]:
    """Intrinsic value by DCF scenarios, owner earnings, EV/EBITDA and residual income vs price.

    Args:
        ticker: Ticker symbol, for example AAPL.
        as_of: Analysis date YYYY-MM-DD; empty means today.
    """
    return await _compute(Domain.VALUATION, ticker, as_of, runtime)


@integration_tool(_meta({Domain.TECHNICALS}, COMPUTE_EVIDENCE[Domain.TECHNICALS]))
async def compute_technicals(
    ticker: str,
    runtime: ToolRuntime[RunContext],
    as_of: str = "",
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> tuple[str, Any]:
    """Trend, mean reversion, momentum, volatility and return statistics over past prices.

    Args:
        ticker: Ticker symbol, for example AAPL.
        as_of: Analysis date YYYY-MM-DD; empty means today.
        lookback_days: Days of history, 90 to 1825.
    """
    days = max(90, min(lookback_days, 1825))
    return await _compute(Domain.TECHNICALS, ticker, as_of, runtime, lookback_days=days)


@integration_tool(_meta({Domain.SENTIMENT}, COMPUTE_EVIDENCE[Domain.SENTIMENT]))
async def compute_sentiment(
    ticker: str, runtime: ToolRuntime[RunContext], as_of: str = ""
) -> tuple[str, Any]:
    """Insider buying versus selling and headline tone over the past year.

    Args:
        ticker: Ticker symbol, for example AAPL.
        as_of: Analysis date YYYY-MM-DD; empty means today.
    """
    return await _compute(Domain.SENTIMENT, ticker, as_of, runtime)


@integration_tool(_meta({Domain.BUFFETT}, COMPUTE_EVIDENCE[Domain.BUFFETT]))
async def compute_buffett(
    ticker: str, runtime: ToolRuntime[RunContext], as_of: str = ""
) -> tuple[str, Any]:
    """Buffett lens: moat, consistency, pricing power, management and owner-earnings value.

    Args:
        ticker: Ticker symbol, for example AAPL.
        as_of: Analysis date YYYY-MM-DD; empty means today.
    """
    return await _compute(Domain.BUFFETT, ticker, as_of, runtime)


@integration_tool(_meta({Domain.GRAHAM}, COMPUTE_EVIDENCE[Domain.GRAHAM]))
async def compute_graham(
    ticker: str, runtime: ToolRuntime[RunContext], as_of: str = ""
) -> tuple[str, Any]:
    """Graham lens: earnings stability, balance-sheet strength, net-net and Graham Number.

    Args:
        ticker: Ticker symbol, for example AAPL.
        as_of: Analysis date YYYY-MM-DD; empty means today.
    """
    return await _compute(Domain.GRAHAM, ticker, as_of, runtime)


DATA_TOOLS: Sequence[BaseTool] = (
    get_prices,
    get_financial_metrics,
    search_line_items,
    get_company_news,
    get_insider_trades,
    get_market_cap,
)
#: One compute tool per analyst domain, in report order.
COMPUTE_TOOLS: dict[Domain, BaseTool] = {
    Domain.FUNDAMENTALS: compute_fundamentals,
    Domain.VALUATION: compute_valuation,
    Domain.TECHNICALS: compute_technicals,
    Domain.SENTIMENT: compute_sentiment,
    Domain.BUFFETT: compute_buffett,
    Domain.GRAHAM: compute_graham,
}
ANALYTICS_TOOLS: tuple[BaseTool, ...] = (*DATA_TOOLS, *COMPUTE_TOOLS.values())
