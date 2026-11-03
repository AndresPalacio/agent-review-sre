"""Reference growth analyst: exact weighted metrics and thresholds from v1."""

from __future__ import annotations

from collections.abc import Sequence

from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.market import FinancialMetrics
from fin_graph.domain.quant.common import Component, QuantScore, Signal, no_data
from fin_graph.domain.tools import Domain


def _trend(values: Sequence[float | None]) -> float:
    clean = [value for value in values if value is not None]
    if len(clean) < 2:
        return 0.0
    x = list(range(len(clean)))
    count = len(clean)
    denominator = count * sum(value**2 for value in x) - sum(x) ** 2
    if denominator == 0:
        return 0.0
    return (
        count * sum(a * b for a, b in zip(x, clean, strict=True)) - sum(x) * sum(clean)
    ) / denominator


def analyze_growth_trends(snapshot: CompanySnapshot) -> Component:
    revenue = [item.revenue_growth for item in snapshot.metrics]
    eps = [item.earnings_per_share_growth for item in snapshot.metrics]
    fcf = [item.free_cash_flow_growth for item in snapshot.metrics]
    score = 0.0
    if revenue and revenue[0] is not None:
        score += 0.4 if revenue[0] > 0.20 else 0.2 if revenue[0] > 0.10 else 0
        score += 0.1 if _trend(revenue) > 0 else 0
    if eps and eps[0] is not None:
        score += 0.25 if eps[0] > 0.20 else 0.1 if eps[0] > 0.10 else 0
        score += 0.05 if _trend(eps) > 0 else 0
    if fcf and fcf[0] is not None and fcf[0] > 0.15:
        score += 0.1
    metrics = {
        "revenue_growth": revenue[0] if revenue else None,
        "revenue_trend": _trend(revenue),
        "eps_growth": eps[0] if eps else None,
        "eps_trend": _trend(eps),
        "fcf_growth": fcf[0] if fcf else None,
        "fcf_trend": _trend(fcf),
    }
    return Component(name="historical_growth", score=min(score, 1), max_score=1, metrics=metrics)


def analyze_valuation(metric: FinancialMetrics) -> Component:
    peg, sales = metric.peg_ratio, metric.price_to_sales_ratio
    score = 0.0
    if peg is not None:
        score += 0.5 if peg < 1 else 0.25 if peg < 2 else 0
    if sales is not None:
        score += 0.5 if sales < 2 else 0.25 if sales < 5 else 0
    return Component(
        name="growth_valuation",
        score=min(score, 1),
        max_score=1,
        metrics={"peg_ratio": peg, "price_to_sales_ratio": sales},
    )


def analyze_margin_trends(snapshot: CompanySnapshot) -> Component:
    gross = [item.gross_margin for item in snapshot.metrics]
    operating = [item.operating_margin for item in snapshot.metrics]
    net = [item.net_margin for item in snapshot.metrics]
    score = 0.0
    if gross and gross[0] is not None:
        score += 0.2 if gross[0] > 0.5 else 0
        score += 0.2 if _trend(gross) > 0 else 0
    if operating and operating[0] is not None:
        score += 0.2 if operating[0] > 0.15 else 0
        score += 0.2 if _trend(operating) > 0 else 0
    score += 0.2 if _trend(net) > 0 else 0
    return Component(
        name="margin_expansion",
        score=min(score, 1),
        max_score=1,
        metrics={
            "gross_margin": gross[0] if gross else None,
            "gross_margin_trend": _trend(gross),
            "operating_margin": operating[0] if operating else None,
            "operating_margin_trend": _trend(operating),
            "net_margin": net[0] if net else None,
            "net_margin_trend": _trend(net),
        },
    )


def analyze_insider_conviction(snapshot: CompanySnapshot) -> Component:
    buys = sum(
        trade.transaction_value
        for trade in snapshot.insider_trades
        if trade.transaction_value and (trade.transaction_shares or 0) > 0
    )
    sells = sum(
        abs(trade.transaction_value)
        for trade in snapshot.insider_trades
        if trade.transaction_value and (trade.transaction_shares or 0) < 0
    )
    net = 0.0 if buys + sells == 0 else (buys - sells) / (buys + sells)
    score = 1.0 if net > 0.5 else 0.7 if net > 0.1 else 0.5 if net > -0.1 else 0.2
    return Component(
        name="insider_conviction",
        score=score,
        max_score=1,
        metrics={"net_flow_ratio": net, "buys": buys, "sells": sells},
    )


def check_financial_health(metric: FinancialMetrics) -> Component:
    score = 1.0
    if metric.debt_to_equity is not None:
        score -= 0.5 if metric.debt_to_equity > 1.5 else 0.2 if metric.debt_to_equity > 0.8 else 0
    if metric.current_ratio is not None:
        score -= 0.5 if metric.current_ratio < 1 else 0.2 if metric.current_ratio < 1.5 else 0
    return Component(
        name="financial_health",
        score=max(score, 0),
        max_score=1,
        metrics={"debt_to_equity": metric.debt_to_equity, "current_ratio": metric.current_ratio},
    )


def score_growth(snapshot: CompanySnapshot) -> QuantScore:
    if not snapshot.metrics:
        return no_data(Domain.FUNDAMENTALS, snapshot.ticker, "financial metrics unavailable")
    components = [
        analyze_growth_trends(snapshot),
        analyze_valuation(snapshot.metrics[0]),
        analyze_margin_trends(snapshot),
        analyze_insider_conviction(snapshot),
        check_financial_health(snapshot.metrics[0]),
    ]
    weights = (0.40, 0.25, 0.15, 0.10, 0.10)
    weighted = sum(
        (component.score or 0) * weight
        for component, weight in zip(components, weights, strict=True)
    )
    signal = (
        Signal.BULLISH if weighted > 0.6 else Signal.BEARISH if weighted < 0.4 else Signal.NEUTRAL
    )
    gaps = [] if snapshot.insider_trades else ["insider trades unavailable"]
    return QuantScore(
        analyst=Domain.FUNDAMENTALS,
        ticker=snapshot.ticker,
        signal=signal,
        confidence=round(abs(weighted - 0.5) * 200),
        score=weighted,
        max_score=1,
        components=components,
        data_gaps=gaps,
    )
