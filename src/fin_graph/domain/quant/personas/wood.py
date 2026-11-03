"""Cathie Wood v1 formulas: disruption, innovation reinvestment, and growth DCF."""

from __future__ import annotations

from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.quant.common import Component, QuantScore, Signal, no_data
from fin_graph.domain.quant.personas import midpoint_confidence
from fin_graph.domain.quant.personas._growth_common import line_values
from fin_graph.domain.tools import Domain

MAX_SCORE = 15.0


def _growth(latest: float, oldest: float) -> float:
    return (latest - oldest) / abs(oldest) if oldest else 0.0


def analyze_disruptive_potential(snapshot: CompanySnapshot) -> Component:
    revenue = [value for value in line_values(snapshot.line_items, "revenue") if value]
    raw = 0.0
    metrics: dict[str, float | None] = {}
    if len(revenue) >= 3:
        rates = [_growth(revenue[index], revenue[index + 1]) for index in range(len(revenue) - 1)]
        latest = rates[0] if rates else 0.0
        raw += 2 if len(rates) >= 2 and rates[0] > rates[-1] else 0
        raw += 3 if latest > 1 else 2 if latest > 0.5 else 1 if latest > 0.2 else 0
        metrics.update(latest_revenue_growth=latest, oldest_revenue_growth=rates[-1])
    gross = [
        value for value in line_values(snapshot.line_items, "gross_margin") if value is not None
    ]
    if len(gross) >= 2:
        trend = gross[0] - gross[-1]
        raw += 2 if trend > 0.05 else 1 if trend > 0 else 0
        raw += 2 if gross[0] > 0.50 else 0
        metrics.update(gross_margin=gross[0], gross_margin_trend=trend)
    opex = [value for value in line_values(snapshot.line_items, "operating_expense") if value]
    if len(revenue) >= 2 and len(opex) >= 2:
        revenue_growth, opex_growth = _growth(revenue[0], revenue[-1]), _growth(opex[0], opex[-1])
        raw += 2 if revenue_growth > opex_growth else 0
        metrics.update(revenue_growth=revenue_growth, operating_expense_growth=opex_growth)
    research = [
        value
        for value in line_values(snapshot.line_items, "research_and_development")
        if value is not None
    ]
    intensity = research[0] / revenue[0] if research and revenue and revenue[0] else None
    if intensity is not None:
        raw += 3 if intensity > 0.15 else 2 if intensity > 0.08 else 1 if intensity > 0.05 else 0
    metrics["research_intensity"] = intensity
    return Component(
        name="disruptive_potential",
        score=raw / 12 * 5,
        max_score=5,
        metrics={"raw_score": raw, "reference_max_score": 12, **metrics},
    )


def analyze_innovation_growth(snapshot: CompanySnapshot) -> Component:
    research = [
        value for value in line_values(snapshot.line_items, "research_and_development") if value
    ]
    revenue = [value for value in line_values(snapshot.line_items, "revenue") if value]
    raw = 0.0
    metrics: dict[str, float | int | None] = {}
    if len(research) >= 2 and revenue:
        rd_growth = _growth(research[0], research[-1])
        raw += 3 if rd_growth > 0.5 else 2 if rd_growth > 0.2 else 0
        start = research[-1] / revenue[-1] if revenue[-1] else 0
        end = research[0] / revenue[0] if revenue[0] else 0
        raw += 2 if end > start else 0
        metrics.update(rd_growth=rd_growth, rd_intensity_start=start, rd_intensity_end=end)
    fcf = [value for value in line_values(snapshot.line_items, "free_cash_flow") if value]
    if len(fcf) >= 2:
        fcf_growth = _growth(fcf[0], fcf[-1])
        positive = sum(value > 0 for value in fcf)
        raw += (
            3
            if fcf_growth > 0.3 and positive == len(fcf)
            else 2
            if positive >= len(fcf) * 0.75
            else 1
            if positive > len(fcf) * 0.5
            else 0
        )
        metrics.update(fcf_growth=fcf_growth, positive_fcf_periods=positive)
    margins = [value for value in line_values(snapshot.line_items, "operating_margin") if value]
    if len(margins) >= 2:
        trend = margins[0] - margins[-1]
        raw += (
            3
            if margins[0] > 0.15 and trend > 0
            else 2
            if margins[0] > 0.10
            else 1
            if trend > 0
            else 0
        )
        metrics.update(operating_margin=margins[0], operating_margin_trend=trend)
    capex = [value for value in line_values(snapshot.line_items, "capital_expenditure") if value]
    if len(capex) >= 2 and revenue:
        intensity = abs(capex[0]) / revenue[0]
        growth = _growth(abs(capex[0]), abs(capex[-1]))
        raw += 2 if intensity > 0.10 and growth > 0.2 else 1 if intensity > 0.05 else 0
        metrics.update(capex_intensity=intensity, capex_growth=growth)
    dividends = [
        value
        for value in line_values(snapshot.line_items, "dividends_and_other_cash_distributions")
        if value
    ]
    if dividends and fcf:
        payout = dividends[0] / fcf[0] if fcf[0] else 1
        raw += 2 if payout < 0.2 else 1 if payout < 0.4 else 0
        metrics["payout_ratio"] = payout
    return Component(
        name="innovation_growth",
        score=raw / 15 * 5,
        max_score=5,
        metrics={"raw_score": raw, "reference_max_score": 15, **metrics},
    )


def analyze_cathie_wood_valuation(snapshot: CompanySnapshot) -> Component:
    fcf_values = [value for value in line_values(snapshot.line_items, "free_cash_flow") if value]
    fcf = fcf_values[0] if fcf_values else 0.0
    if fcf <= 0 or snapshot.market_cap is None:
        return Component(
            name="cathie_wood_valuation",
            score=0,
            max_score=3,
            metrics={"free_cash_flow": fcf, "intrinsic_value": None},
        )
    growth_rate, discount_rate, terminal_multiple, years = 0.20, 0.15, 25, 5
    present = sum(
        fcf * (1 + growth_rate) ** year / (1 + discount_rate) ** year
        for year in range(1, years + 1)
    )
    terminal = fcf * (1 + growth_rate) ** years * terminal_multiple / (1 + discount_rate) ** years
    intrinsic = present + terminal
    margin = (intrinsic - snapshot.market_cap) / snapshot.market_cap
    score = 3.0 if margin > 0.5 else 1.0 if margin > 0.2 else 0.0
    return Component(
        name="cathie_wood_valuation",
        score=score,
        max_score=3,
        metrics={
            "free_cash_flow": fcf,
            "intrinsic_value": intrinsic,
            "market_cap": snapshot.market_cap,
            "margin_of_safety": margin,
        },
    )


def score_wood(snapshot: CompanySnapshot) -> QuantScore:
    if not snapshot.metrics or not snapshot.line_items:
        return no_data(Domain.FUNDAMENTALS, snapshot.ticker, "insufficient innovation data")
    components = [
        analyze_disruptive_potential(snapshot),
        analyze_innovation_growth(snapshot),
        analyze_cathie_wood_valuation(snapshot),
    ]
    total = sum(component.score or 0 for component in components)
    signal = Signal.BULLISH if total >= 10.5 else Signal.BEARISH if total <= 4.5 else Signal.NEUTRAL
    return QuantScore(
        analyst=Domain.FUNDAMENTALS,
        ticker=snapshot.ticker,
        signal=signal,
        confidence=midpoint_confidence(total, MAX_SCORE),
        score=total,
        max_score=MAX_SCORE,
        components=components,
    )
