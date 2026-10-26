"""Risk: a position limit per ticker from its volatility and its correlation with the book.

Ported from ai-hedge-fund ``src/agents/risk_manager.py`` (MIT). A 20% base allocation is scaled
by volatility (x1.25 below 15% annualised down to x0.5 above 50%) and by the average correlation
with the active positions (x0.70 to x1.10), applied to the net liquidation value. The limit is
then capped by the cash available. The LLM never sizes a position: this limit is a hard bound.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date
from itertools import pairwise

from pydantic import BaseModel, ConfigDict, Field

from fin_graph.domain.market import Price
from fin_graph.domain.portfolio import Portfolio
from fin_graph.domain.quant import series

TRADING_DAYS = 252
BASE_LIMIT = 0.20
#: Assumed when a ticker has no usable prices: 5% a day, the riskiest bucket.
FALLBACK_DAILY_VOLATILITY = 0.05


class VolatilityMetrics(BaseModel):
    """Recent volatility and where it sits in the ticker's own history (percentile 0-100)."""

    model_config = ConfigDict(frozen=True)

    daily: float
    annualized: float
    percentile: float
    data_points: int


class CorrelationMetrics(BaseModel):
    """How the ticker moves with the rest of the book."""

    model_config = ConfigDict(frozen=True)

    average: float | None = None
    maximum: float | None = None
    top: list[tuple[str, float]] = Field(default_factory=list)


class PositionLimit(BaseModel):
    """The most notional the ticker may still take, and every factor that set it."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    current_price: float
    remaining_limit: float
    volatility: VolatilityMetrics
    correlation: CorrelationMetrics = Field(default_factory=CorrelationMetrics)
    portfolio_value: float = 0.0
    current_position_value: float = 0.0
    volatility_limit_pct: float = 0.0
    correlation_multiplier: float = 1.0
    limit_pct: float = 0.0
    error: str | None = None

    @property
    def max_shares(self) -> int:
        """Whole shares the remaining limit buys at the current price."""
        if self.current_price <= 0 or self.remaining_limit <= 0:
            return 0
        return int(self.remaining_limit // self.current_price)


def _fallback_volatility(points: int) -> VolatilityMetrics:
    return VolatilityMetrics(
        daily=FALLBACK_DAILY_VOLATILITY,
        annualized=FALLBACK_DAILY_VOLATILITY * math.sqrt(TRADING_DAYS),
        percentile=100,
        data_points=points,
    )


def volatility_metrics(prices: Sequence[Price], lookback_days: int = 60) -> VolatilityMetrics:
    """Sample volatility of the last ``lookback_days`` returns and its 30-day rolling percentile."""
    returns = [
        value for value in series.pct_change([bar.close for bar in prices]) if not math.isnan(value)
    ]
    if len(returns) < 2:
        return _fallback_volatility(len(returns))
    recent = returns[-lookback_days:]
    daily = series.last(series.rolling_std(recent, len(recent)))
    if daily is None:
        return VolatilityMetrics(
            daily=0.025, annualized=0.25, percentile=50, data_points=len(recent)
        )
    percentile = 50.0
    if len(returns) >= 30:
        rolling = [value for value in series.rolling_std(returns, 30) if not math.isnan(value)]
        if rolling:
            percentile = sum(value <= daily for value in rolling) / len(rolling) * 100
    return VolatilityMetrics(
        daily=daily,
        annualized=daily * math.sqrt(TRADING_DAYS),
        percentile=percentile,
        data_points=len(recent),
    )


def volatility_limit_pct(annualized: float) -> float:
    """The share of the portfolio a ticker may hold for its annualised volatility (5%-25%)."""
    if annualized < 0.15:
        multiplier = 1.25
    elif annualized < 0.30:
        multiplier = 1.0 - (annualized - 0.15) * 0.5
    elif annualized < 0.50:
        multiplier = 0.75 - (annualized - 0.30) * 0.5
    else:
        multiplier = 0.50
    return BASE_LIMIT * max(0.25, min(1.25, multiplier))


def correlation_multiplier(average: float) -> float:
    """Shrink the limit for a ticker that moves with the book, grow it for a diversifier."""
    if average >= 0.80:
        return 0.70
    if average >= 0.60:
        return 0.85
    if average >= 0.40:
        return 1.00
    if average >= 0.20:
        return 1.05
    return 1.10


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    n = len(left)
    if n < 2:
        return None
    mean_l, mean_r = sum(left) / n, sum(right) / n
    cov = sum((a - mean_l) * (b - mean_r) for a, b in zip(left, right, strict=True))
    var_l = sum((a - mean_l) ** 2 for a in left)
    var_r = sum((b - mean_r) ** 2 for b in right)
    if var_l == 0 or var_r == 0:
        return None
    return cov / math.sqrt(var_l * var_r)


def correlation_matrix(
    prices: Mapping[str, Sequence[Price]], min_overlap: int = 5
) -> dict[str, dict[str, float]]:
    """Pairwise correlation of daily returns over the days every ticker traded.

    Like ``DataFrame.dropna(how="any").corr()``: only dates where all tickers have a return
    count, and fewer than ``min_overlap`` such dates (or fewer than two tickers) gives nothing.
    """
    returns: dict[str, dict[date, float]] = {}
    for ticker, bars in prices.items():
        ordered = sorted(bars, key=lambda bar: bar.time)
        by_day: dict[date, float] = {}
        for previous, current in pairwise(ordered):
            if previous.close:
                by_day[current.day] = current.close / previous.close - 1
        if by_day:
            returns[ticker] = by_day
    if len(returns) < 2:
        return {}
    common = set.intersection(*(set(days) for days in returns.values()))
    if len(common) < min_overlap:
        return {}
    days = sorted(common)
    aligned = {ticker: [values[day] for day in days] for ticker, values in returns.items()}
    matrix: dict[str, dict[str, float]] = {}
    for left, left_values in aligned.items():
        for right, right_values in aligned.items():
            value = 1.0 if left == right else _pearson(left_values, right_values)
            if value is not None:
                matrix.setdefault(left, {})[right] = value
    return matrix


def position_limits(
    tickers: Sequence[str], prices: Mapping[str, Sequence[Price]], portfolio: Portfolio
) -> dict[str, PositionLimit]:
    """Volatility- and correlation-adjusted remaining limits for ``tickers``.

    ``prices`` should also hold the tickers already in the portfolio, so their value counts and
    their correlation is known. Prices are point-in-time: pass only bars up to the analysis date.
    """
    current: dict[str, float] = {}
    volatility: dict[str, VolatilityMetrics] = {}
    for ticker in {*tickers, *portfolio.positions}:
        bars = sorted(prices.get(ticker, ()), key=lambda bar: bar.time)
        if len(bars) > 1:
            current[ticker] = bars[-1].close
            volatility[ticker] = volatility_metrics(bars)
        else:
            volatility[ticker] = _fallback_volatility(len(bars))
    matrix = correlation_matrix(prices)
    active = portfolio.active_tickers()
    total = portfolio.net_liquidation_value(current)
    limits: dict[str, PositionLimit] = {}
    for ticker in tickers:
        price = current.get(ticker, 0.0)
        if price <= 0:
            limits[ticker] = PositionLimit(
                ticker=ticker,
                current_price=0.0,
                remaining_limit=0.0,
                volatility=volatility[ticker],
                error="Missing price data for risk calculation",
            )
            continue
        held_value = portfolio.position(ticker).exposure(price)
        vol_pct = volatility_limit_pct(volatility[ticker].annualized)
        correlation, multiplier = CorrelationMetrics(), 1.0
        row = matrix.get(ticker, {})
        peers = [other for other in active if other in row and other != ticker] or [
            other for other in row if other != ticker
        ]
        if peers:
            values = sorted(((other, row[other]) for other in peers), key=lambda pair: -pair[1])
            average = sum(value for _, value in values) / len(values)
            correlation = CorrelationMetrics(average=average, maximum=values[0][1], top=values[:3])
            multiplier = correlation_multiplier(average)
        limit_pct = vol_pct * multiplier
        remaining = total * limit_pct - held_value
        limits[ticker] = PositionLimit(
            ticker=ticker,
            current_price=price,
            remaining_limit=min(remaining, portfolio.cash),
            volatility=volatility[ticker],
            correlation=correlation,
            portfolio_value=total,
            current_position_value=held_value,
            volatility_limit_pct=vol_pct,
            correlation_multiplier=multiplier,
            limit_pct=limit_pct,
        )
    return limits
