"""Generic growth analyst scorer from growth, margins, valuation and health."""

from __future__ import annotations

from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.quant.common import Component, QuantScore, no_data, score_signal
from fin_graph.domain.quant.personas import component_total, midpoint_confidence
from fin_graph.domain.quant.personas._growth_common import (
    growth_component,
    health_component,
    insider_component,
    valuation_component,
)
from fin_graph.domain.tools import Domain

MAX_SCORE = 18.0


def analyze_growth_trends(snapshot: CompanySnapshot) -> Component:
    return growth_component(snapshot)


def analyze_valuation(snapshot: CompanySnapshot) -> Component:
    return valuation_component(snapshot)


def analyze_margin_trends(snapshot: CompanySnapshot) -> Component:
    latest = snapshot.metrics[0] if snapshot.metrics else None
    gross = latest.gross_margin if latest else None
    operating = latest.operating_margin if latest else None
    score = (1 if gross is not None and gross > 0.3 else 0) + (
        1 if operating is not None and operating > 0.15 else 0
    )
    return Component(
        name="margin_trends",
        score=score,
        max_score=2,
        details=f"Gross margin {gross}; operating margin {operating}",
        metrics={"gross_margin": gross, "operating_margin": operating},
    )


def analyze_insider_conviction(snapshot: CompanySnapshot) -> Component:
    return insider_component(snapshot, "insider_conviction")


def check_financial_health(snapshot: CompanySnapshot) -> Component:
    return health_component(snapshot)


def score_growth(snapshot: CompanySnapshot) -> QuantScore:
    if not snapshot.metrics and not snapshot.line_items:
        return no_data(Domain.FUNDAMENTALS, snapshot.ticker, "no growth observations")
    components = [
        analyze_growth_trends(snapshot),
        analyze_valuation(snapshot),
        analyze_margin_trends(snapshot),
        analyze_insider_conviction(snapshot),
        check_financial_health(snapshot),
    ]
    total = component_total(components)
    gaps = []
    if not snapshot.metrics:
        gaps.append("financial metrics unavailable")
    if not snapshot.line_items:
        gaps.append("annual line items unavailable")
    if not snapshot.insider_trades:
        gaps.append("insider trades unavailable")
    return QuantScore(
        analyst=Domain.FUNDAMENTALS,
        ticker=snapshot.ticker,
        signal=score_signal(total, MAX_SCORE),
        confidence=midpoint_confidence(total, MAX_SCORE),
        score=total,
        max_score=MAX_SCORE,
        components=components,
        data_gaps=gaps,
    )
