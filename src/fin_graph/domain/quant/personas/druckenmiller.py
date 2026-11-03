"""Stanley Druckenmiller-style growth, momentum, conviction and risk-reward scoring."""

from __future__ import annotations

from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.quant.common import Component, QuantScore, no_data, score_signal
from fin_graph.domain.quant.personas import component_total, midpoint_confidence
from fin_graph.domain.quant.personas._growth_common import (
    growth_component,
    health_component,
    insider_component,
    momentum_component,
    sentiment_component,
    valuation_component,
)
from fin_graph.domain.tools import Domain

MAX_SCORE = 23.0


def analyze_growth_and_momentum(snapshot: CompanySnapshot) -> Component:
    growth = growth_component(snapshot)
    momentum = momentum_component(snapshot)
    return Component(
        name="growth_and_momentum",
        score=(growth.score or 0) + (momentum.score or 0),
        max_score=11,
        details=f"{growth.details}; {momentum.details}",
        metrics={"growth": growth.metrics, "momentum": momentum.metrics},
    )


def analyze_insider_activity(snapshot: CompanySnapshot) -> Component:
    return insider_component(snapshot)


def analyze_sentiment(snapshot: CompanySnapshot) -> Component:
    return sentiment_component(snapshot)


def analyze_risk_reward(snapshot: CompanySnapshot) -> Component:
    return health_component(snapshot, "risk_reward")


def analyze_druckenmiller_valuation(snapshot: CompanySnapshot) -> Component:
    return valuation_component(snapshot, "druckenmiller_valuation")


def score_druckenmiller(snapshot: CompanySnapshot) -> QuantScore:
    if not snapshot.metrics and not snapshot.line_items and not snapshot.prices:
        return no_data(Domain.TECHNICALS, snapshot.ticker, "no macro or price observations")
    components = [
        analyze_growth_and_momentum(snapshot),
        analyze_insider_activity(snapshot),
        analyze_sentiment(snapshot),
        analyze_risk_reward(snapshot),
        analyze_druckenmiller_valuation(snapshot),
    ]
    total = component_total(components)
    gaps = []
    if len(snapshot.prices) < 20:
        gaps.append("insufficient price history")
    if not snapshot.insider_trades:
        gaps.append("insider trades unavailable")
    if not snapshot.news:
        gaps.append("company news unavailable")
    return QuantScore(
        analyst=Domain.TECHNICALS,
        ticker=snapshot.ticker,
        signal=score_signal(total, MAX_SCORE),
        confidence=midpoint_confidence(total, MAX_SCORE),
        score=total,
        max_score=MAX_SCORE,
        components=components,
        data_gaps=gaps,
    )
