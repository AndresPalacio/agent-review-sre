"""Phil Fisher-style growth quality, margins, management and scuttlebutt scoring."""

from __future__ import annotations

from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.quant.common import Component, QuantScore, no_data, score_signal
from fin_graph.domain.quant.personas import component_total, midpoint_confidence
from fin_graph.domain.quant.personas._growth_common import (
    growth_component,
    health_component,
    insider_component,
    sentiment_component,
    valuation_component,
)
from fin_graph.domain.tools import Domain

MAX_SCORE = 22.0


def analyze_fisher_growth_quality(snapshot: CompanySnapshot) -> Component:
    return growth_component(snapshot, "fisher_growth_quality")


def analyze_margins_stability(snapshot: CompanySnapshot) -> Component:
    latest = snapshot.metrics[0] if snapshot.metrics else None
    gross = latest.gross_margin if latest else None
    operating = latest.operating_margin if latest else None
    score = (1 if gross is not None and gross >= 0.3 else 0) + (
        1 if operating is not None and operating >= 0.15 else 0
    )
    return Component(
        name="margins_stability",
        score=score,
        max_score=2,
        details=f"Gross margin {gross}; operating margin {operating}",
        metrics={"gross_margin": gross, "operating_margin": operating},
    )


def analyze_management_efficiency_leverage(snapshot: CompanySnapshot) -> Component:
    return health_component(snapshot, "management_efficiency_leverage")


def analyze_fisher_valuation(snapshot: CompanySnapshot) -> Component:
    return valuation_component(snapshot, "fisher_valuation")


def analyze_insider_activity(snapshot: CompanySnapshot) -> Component:
    return insider_component(snapshot)


def analyze_sentiment(snapshot: CompanySnapshot) -> Component:
    return sentiment_component(snapshot)


def score_fisher(snapshot: CompanySnapshot) -> QuantScore:
    if not snapshot.metrics and not snapshot.line_items:
        return no_data(Domain.FUNDAMENTALS, snapshot.ticker, "no scuttlebutt observations")
    components = [
        analyze_fisher_growth_quality(snapshot),
        analyze_margins_stability(snapshot),
        analyze_management_efficiency_leverage(snapshot),
        analyze_fisher_valuation(snapshot),
        analyze_insider_activity(snapshot),
        analyze_sentiment(snapshot),
    ]
    total = component_total(components)
    gaps = []
    if not snapshot.insider_trades:
        gaps.append("insider trades unavailable")
    if not snapshot.news:
        gaps.append("company news unavailable")
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
