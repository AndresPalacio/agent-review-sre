"""Point-in-time fundamental and technical feature snapshots."""

from __future__ import annotations

from datetime import date, timedelta

from fin_graph.quant.data import (
    FundamentalSnapshot,
    MarketDataClient,
    PointInTimeData,
    TechnicalSnapshot,
    adjusted_close,
)


class InsufficientData(ValueError):
    """A requested feature snapshot cannot be computed defensibly."""


def _average(values: list[float | None]) -> float | None:
    known = [value for value in values if value is not None]
    return round(sum(known) / len(known), 4) if known else None


def _trend(values: list[float | None]) -> float | None:
    known = [value for value in values if value is not None]
    return round(known[0] - known[-1], 4) if len(known) >= 2 else None


def _quarterly_cagr(values: list[float | None]) -> float | None:
    known = [value for value in values if value is not None]
    if len(known) < 2 or known[0] <= 0 or known[-1] <= 0:
        return None
    years = (len(known) - 1) / 4
    return round(float((known[0] / known[-1]) ** (1 / years) - 1), 4)


async def build_fundamental_snapshot(
    ticker: str,
    as_of: date,
    data: MarketDataClient,
    *,
    periods: int = 20,
    minimum_periods: int = 4,
) -> FundamentalSnapshot:
    """Build the filed TTM history used by fundamental and LLM alpha models."""

    metrics = await PointInTimeData(data, as_of).financial_metrics(
        ticker, as_of, period="ttm", limit=periods
    )
    if len(metrics) < minimum_periods:
        raise InsufficientData(
            f"{ticker} as of {as_of.isoformat()}: {len(metrics)} periods; need {minimum_periods}"
        )
    return FundamentalSnapshot(
        ticker=ticker,
        as_of=as_of,
        periods=tuple(metrics),
        roe_average=_average([item.return_on_equity for item in metrics]),
        net_margin_average=_average([item.net_margin for item in metrics]),
        gross_margin_trend=_trend([item.gross_margin for item in metrics]),
        book_value_per_share_cagr=_quarterly_cagr([item.book_value_per_share for item in metrics]),
        debt_to_equity_latest=metrics[0].debt_to_equity,
        market_cap_latest=metrics[0].market_cap,
    )


async def build_technical_snapshot(
    ticker: str,
    as_of: date,
    data: MarketDataClient,
    *,
    start: date | None = None,
) -> TechnicalSnapshot:
    """Build adjusted price history, oldest first, with no post-cutoff bar."""

    first = start or as_of - timedelta(days=1095)
    prices = await PointInTimeData(data, as_of).prices(ticker, first, as_of)
    if not prices:
        raise InsufficientData(f"{ticker} as of {as_of.isoformat()}: no prices")
    return TechnicalSnapshot(
        ticker=ticker,
        as_of=as_of,
        prices=tuple(prices),
        closes=tuple(adjusted_close(item) for item in prices),
    )
