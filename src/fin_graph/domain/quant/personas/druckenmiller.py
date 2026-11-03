"""Stanley Druckenmiller v1 growth/momentum, risk and valuation formulas."""

from __future__ import annotations

import statistics

from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.quant.common import Component, QuantScore, Signal, no_data
from fin_graph.domain.quant.personas import midpoint_confidence
from fin_graph.domain.quant.personas._growth_common import line_values
from fin_graph.domain.quant.personas.lynch import analyze_insider_activity, analyze_sentiment
from fin_graph.domain.tools import Domain


def _cagr(values: list[float]) -> float | None:
    return (
        (values[0] / values[-1]) ** (1 / (len(values) - 1)) - 1
        if len(values) >= 2 and values[0] > 0 and values[-1] > 0
        else None
    )


def analyze_growth_and_momentum(snapshot: CompanySnapshot) -> Component:
    revenue = line_values(snapshot.line_items, "revenue")
    eps = line_values(snapshot.line_items, "earnings_per_share")
    revenue_growth, eps_growth = _cagr(revenue), _cagr(eps)
    raw = 0.0
    for value in (revenue_growth, eps_growth):
        if value is not None:
            raw += 3 if value > 0.08 else 2 if value > 0.04 else 1 if value > 0.01 else 0
    momentum: float | None = None
    if len(snapshot.prices) > 30 and snapshot.prices[0].close > 0:
        momentum = (snapshot.prices[-1].close - snapshot.prices[0].close) / snapshot.prices[0].close
        raw += 3 if momentum > 0.50 else 2 if momentum > 0.20 else 1 if momentum > 0 else 0
    return Component(
        name="growth_and_momentum",
        score=min(10, raw / 9 * 10),
        max_score=10,
        metrics={
            "revenue_cagr": revenue_growth,
            "eps_cagr": eps_growth,
            "price_momentum": momentum,
        },
    )


def analyze_risk_reward(snapshot: CompanySnapshot) -> Component:
    debt = line_values(snapshot.line_items, "total_debt")
    equity = line_values(snapshot.line_items, "shareholders_equity")
    debt_to_equity = (
        debt[0] / (equity[0] or 1e-9) if debt and equity and len(debt) == len(equity) else None
    )
    raw = 0.0
    if debt_to_equity is not None:
        raw += (
            3
            if debt_to_equity < 0.3
            else 2
            if debt_to_equity < 0.7
            else 1
            if debt_to_equity < 1.5
            else 0
        )
    returns = [
        current.close / previous.close - 1
        for previous, current in zip(snapshot.prices, snapshot.prices[1:], strict=False)
        if previous.close > 0
    ]
    volatility = statistics.pstdev(returns) if len(snapshot.prices) > 10 and returns else None
    if volatility is not None:
        raw += 3 if volatility < 0.01 else 2 if volatility < 0.02 else 1 if volatility < 0.04 else 0
    return Component(
        name="risk_reward",
        score=min(10, raw / 6 * 10),
        max_score=10,
        metrics={"debt_to_equity": debt_to_equity, "daily_volatility": volatility},
    )


def analyze_druckenmiller_valuation(snapshot: CompanySnapshot) -> Component:
    income = line_values(snapshot.line_items, "net_income")
    fcf = line_values(snapshot.line_items, "free_cash_flow")
    ebit = line_values(snapshot.line_items, "ebit")
    ebitda = line_values(snapshot.line_items, "ebitda")
    debt = line_values(snapshot.line_items, "total_debt")
    cash = line_values(snapshot.line_items, "cash_and_equivalents")
    market_cap = snapshot.market_cap
    enterprise = (
        market_cap + (debt[0] if debt else 0) - (cash[0] if cash else 0)
        if market_cap is not None
        else None
    )
    values = {
        "price_to_earnings": market_cap / income[0]
        if market_cap and income and income[0] > 0
        else None,
        "price_to_free_cash_flow": market_cap / fcf[0]
        if market_cap and fcf and fcf[0] > 0
        else None,
        "enterprise_to_ebit": enterprise / ebit[0]
        if enterprise and enterprise > 0 and ebit and ebit[0] > 0
        else None,
        "enterprise_to_ebitda": enterprise / ebitda[0]
        if enterprise and enterprise > 0 and ebitda and ebitda[0] > 0
        else None,
    }
    raw = 0.0
    for name, value in values.items():
        if value is None:
            continue
        first, second = (10, 18) if name == "enterprise_to_ebitda" else (15, 25)
        raw += 2 if value < first else 1 if value < second else 0
    return Component(
        name="druckenmiller_valuation",
        score=min(10, raw / 8 * 10),
        max_score=10,
        metrics={"enterprise_value": enterprise, **values},
    )


def score_druckenmiller(snapshot: CompanySnapshot) -> QuantScore:
    if not snapshot.line_items and not snapshot.prices:
        return no_data(Domain.TECHNICALS, snapshot.ticker, "no macro or price observations")
    components = [
        analyze_growth_and_momentum(snapshot),
        analyze_risk_reward(snapshot),
        analyze_druckenmiller_valuation(snapshot),
        analyze_sentiment(snapshot),
        analyze_insider_activity(snapshot),
    ]
    weights = (0.35, 0.20, 0.20, 0.15, 0.10)
    total = sum(
        (component.score or 0) * weight
        for component, weight in zip(components, weights, strict=True)
    )
    signal = Signal.BULLISH if total >= 7.5 else Signal.BEARISH if total <= 4.5 else Signal.NEUTRAL
    gaps = []
    if len(snapshot.prices) <= 30:
        gaps.append("insufficient price history")
    if not snapshot.insider_trades:
        gaps.append("insider trades unavailable")
    if not snapshot.news:
        gaps.append("company news unavailable")
    return QuantScore(
        analyst=Domain.TECHNICALS,
        ticker=snapshot.ticker,
        signal=signal,
        confidence=midpoint_confidence(total, 10),
        score=total,
        max_score=10,
        components=components,
        data_gaps=gaps,
    )
