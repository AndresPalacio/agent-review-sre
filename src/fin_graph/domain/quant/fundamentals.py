"""Fundamentals: profitability, growth, financial health and price ratios on the latest TTM.

Ported from ai-hedge-fund ``src/agents/fundamentals.py`` (MIT); thresholds are unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence

from fin_graph.domain.market import FinancialMetrics
from fin_graph.domain.quant.common import (
    Component,
    QuantScore,
    Signal,
    majority,
    no_data,
    num,
    pct,
)
from fin_graph.domain.tools import Domain


def _above(pairs: Sequence[tuple[float | None, float]]) -> int:
    return sum(value is not None and value > threshold for value, threshold in pairs)


def _vote(points: int, *, inverted: bool = False) -> Signal:
    """Two or more hits lean one way, none leans the other, one is neutral."""
    if points >= 2:
        return Signal.BEARISH if inverted else Signal.BULLISH
    if points == 0:
        return Signal.BULLISH if inverted else Signal.BEARISH
    return Signal.NEUTRAL


def profitability(metrics: FinancialMetrics) -> Component:
    """ROE above 15%, net margin above 20%, operating margin above 15%."""
    roe, net, operating = metrics.return_on_equity, metrics.net_margin, metrics.operating_margin
    points = _above([(roe, 0.15), (net, 0.20), (operating, 0.15)])
    return Component(
        name="profitability",
        signal=_vote(points),
        score=points,
        max_score=3,
        details=f"ROE: {pct(roe)}, Net Margin: {pct(net)}, Op Margin: {pct(operating)}",
        metrics={"return_on_equity": roe, "net_margin": net, "operating_margin": operating},
    )


def growth(metrics: FinancialMetrics) -> Component:
    """Revenue, earnings and book value each growing above 10%."""
    revenue, earnings, book = (
        metrics.revenue_growth,
        metrics.earnings_growth,
        metrics.book_value_growth,
    )
    points = _above([(revenue, 0.10), (earnings, 0.10), (book, 0.10)])
    return Component(
        name="growth",
        signal=_vote(points),
        score=points,
        max_score=3,
        details=f"Revenue Growth: {pct(revenue)}, Earnings Growth: {pct(earnings)}, "
        f"Book Value Growth: {pct(book)}",
        metrics={"revenue_growth": revenue, "earnings_growth": earnings, "book_value_growth": book},
    )


def financial_health(metrics: FinancialMetrics) -> Component:
    """Current ratio above 1.5, debt/equity below 0.5, FCF/share above 80% of EPS."""
    current, leverage = metrics.current_ratio, metrics.debt_to_equity
    fcf, eps = metrics.free_cash_flow_per_share, metrics.earnings_per_share
    points = 0
    if current and current > 1.5:
        points += 1
    if leverage and leverage < 0.5:
        points += 1
    if fcf and eps and fcf > eps * 0.8:
        points += 1
    return Component(
        name="financial_health",
        signal=_vote(points),
        score=points,
        max_score=3,
        details=f"Current Ratio: {num(current)}, D/E: {num(leverage)}, "
        f"FCF/share: {num(fcf)} vs EPS: {num(eps)}",
        metrics={
            "current_ratio": current,
            "debt_to_equity": leverage,
            "free_cash_flow_per_share": fcf,
            "earnings_per_share": eps,
        },
    )


def price_ratios(metrics: FinancialMetrics) -> Component:
    """P/E above 25, P/B above 3 and P/S above 5 count against: expensive is bearish."""
    pe, pb, ps = (
        metrics.price_to_earnings_ratio,
        metrics.price_to_book_ratio,
        metrics.price_to_sales_ratio,
    )
    points = _above([(pe, 25), (pb, 3), (ps, 5)])
    return Component(
        name="price_ratios",
        signal=_vote(points, inverted=True),
        score=points,
        max_score=3,
        details=f"P/E: {num(pe)}, P/B: {num(pb)}, P/S: {num(ps)}",
        metrics={"price_to_earnings": pe, "price_to_book": pb, "price_to_sales": ps},
    )


def score_fundamentals(ticker: str, metrics: Sequence[FinancialMetrics]) -> QuantScore:
    """Majority vote of the four sub-analyses on the most recent period (``metrics[0]``)."""
    if not metrics:
        return no_data(Domain.FUNDAMENTALS, ticker, "no financial metrics")
    latest = metrics[0]
    components = [
        profitability(latest),
        growth(latest),
        financial_health(latest),
        price_ratios(latest),
    ]
    signal, confidence = majority([item.signal for item in components])
    gaps = [
        f"{name} missing"
        for item in components
        for name, value in item.metrics.items()
        if value is None
    ]
    return QuantScore(
        analyst=Domain.FUNDAMENTALS,
        ticker=ticker,
        signal=signal,
        confidence=confidence,
        components=components,
        data_gaps=gaps,
    )
