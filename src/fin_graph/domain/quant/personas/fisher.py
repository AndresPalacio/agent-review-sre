"""Phil Fisher v1 weighted quality scorer on six reference 0-10 components."""

from __future__ import annotations

import statistics

from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.quant.common import Component, QuantScore, Signal, no_data
from fin_graph.domain.quant.personas import midpoint_confidence
from fin_graph.domain.quant.personas._growth_common import line_values
from fin_graph.domain.quant.personas.lynch import analyze_insider_activity, analyze_sentiment
from fin_graph.domain.tools import Domain


def _cagr(values: list[float]) -> float | None:
    if len(values) < 2 or values[0] <= 0 or values[-1] <= 0:
        return None
    return float((values[0] / values[-1]) ** (1 / (len(values) - 1)) - 1)


def analyze_fisher_growth_quality(snapshot: CompanySnapshot) -> Component:
    revenue = line_values(snapshot.line_items, "revenue")
    eps = line_values(snapshot.line_items, "earnings_per_share")
    research = line_values(snapshot.line_items, "research_and_development")
    revenue_growth, eps_growth = _cagr(revenue), _cagr(eps)
    raw = 0.0
    for value in (revenue_growth, eps_growth):
        if value is not None:
            raw += 3 if value > 0.20 else 2 if value > 0.10 else 1 if value > 0.03 else 0
    research_ratio = (
        research[0] / (revenue[0] or 1e-9) if research and len(research) == len(revenue) else None
    )
    if research_ratio is not None:
        raw += (
            3
            if 0.03 <= research_ratio <= 0.15
            else 2
            if research_ratio > 0.15
            else 1
            if research_ratio > 0
            else 0
        )
    return Component(
        name="fisher_growth_quality",
        score=min(10, raw / 9 * 10),
        max_score=10,
        metrics={
            "revenue_cagr": revenue_growth,
            "eps_cagr": eps_growth,
            "research_to_revenue": research_ratio,
        },
    )


def analyze_margins_stability(snapshot: CompanySnapshot) -> Component:
    operating = line_values(snapshot.line_items, "operating_margin")
    gross = line_values(snapshot.line_items, "gross_margin")
    raw = 0.0
    if len(operating) >= 2:
        raw += 2 if operating[0] >= operating[-1] > 0 else 1 if operating[0] > 0 else 0
    if gross:
        raw += 2 if gross[0] > 0.5 else 1 if gross[0] > 0.3 else 0
    deviation = statistics.pstdev(operating) if len(operating) >= 3 else None
    if deviation is not None:
        raw += 2 if deviation < 0.02 else 1 if deviation < 0.05 else 0
    return Component(
        name="margins_stability",
        score=min(10, raw / 6 * 10),
        max_score=10,
        metrics={
            "operating_margin": operating[0] if operating else None,
            "oldest_operating_margin": operating[-1] if operating else None,
            "gross_margin": gross[0] if gross else None,
            "operating_margin_stdev": deviation,
        },
    )


def analyze_management_efficiency_leverage(snapshot: CompanySnapshot) -> Component:
    income = line_values(snapshot.line_items, "net_income")
    equity = line_values(snapshot.line_items, "shareholders_equity")
    debt = line_values(snapshot.line_items, "total_debt")
    fcf = line_values(snapshot.line_items, "free_cash_flow")
    raw = 0.0
    roe = (
        income[0] / (equity[0] or 1e-9)
        if income and len(income) == len(equity) and income[0] > 0
        else None
    )
    if roe is not None:
        raw += 3 if roe > 0.2 else 2 if roe > 0.1 else 1 if roe > 0 else 0
    debt_to_equity = debt[0] / (equity[0] or 1e-9) if debt and len(debt) == len(equity) else None
    if debt_to_equity is not None:
        raw += 2 if debt_to_equity < 0.3 else 1 if debt_to_equity < 1 else 0
    positive_fcf = sum(value > 0 for value in fcf)
    fcf_ratio = positive_fcf / len(fcf) if len(fcf) >= 2 else None
    raw += 1 if fcf_ratio is not None and fcf_ratio > 0.8 else 0
    return Component(
        name="management_efficiency_leverage",
        score=min(10, raw / 6 * 10),
        max_score=10,
        metrics={
            "return_on_equity": roe,
            "debt_to_equity": debt_to_equity,
            "positive_fcf_ratio": fcf_ratio,
        },
    )


def analyze_fisher_valuation(snapshot: CompanySnapshot) -> Component:
    income = line_values(snapshot.line_items, "net_income")
    fcf = line_values(snapshot.line_items, "free_cash_flow")
    pe = (
        snapshot.market_cap / income[0]
        if snapshot.market_cap and income and income[0] > 0
        else None
    )
    price_to_fcf = (
        snapshot.market_cap / fcf[0] if snapshot.market_cap and fcf and fcf[0] > 0 else None
    )
    raw = 0.0
    for value in (pe, price_to_fcf):
        if value is not None:
            raw += 2 if value < 20 else 1 if value < 30 else 0
    return Component(
        name="fisher_valuation",
        score=min(10, raw / 4 * 10),
        max_score=10,
        metrics={"price_to_earnings": pe, "price_to_free_cash_flow": price_to_fcf},
    )


def score_fisher(snapshot: CompanySnapshot) -> QuantScore:
    if not snapshot.line_items:
        return no_data(Domain.FUNDAMENTALS, snapshot.ticker, "financial line items unavailable")
    components = [
        analyze_fisher_growth_quality(snapshot),
        analyze_margins_stability(snapshot),
        analyze_management_efficiency_leverage(snapshot),
        analyze_fisher_valuation(snapshot),
        analyze_insider_activity(snapshot),
        analyze_sentiment(snapshot),
    ]
    weights = (0.30, 0.25, 0.20, 0.15, 0.05, 0.05)
    total = sum(
        (component.score or 0) * weight
        for component, weight in zip(components, weights, strict=True)
    )
    signal = Signal.BULLISH if total >= 7.5 else Signal.BEARISH if total <= 4.5 else Signal.NEUTRAL
    gaps = []
    if not snapshot.insider_trades:
        gaps.append("insider trades unavailable")
    if not snapshot.news:
        gaps.append("company news unavailable")
    return QuantScore(
        analyst=Domain.FUNDAMENTALS,
        ticker=snapshot.ticker,
        signal=signal,
        confidence=midpoint_confidence(total, 10),
        score=total,
        max_score=10,
        components=components,
        data_gaps=gaps,
    )
