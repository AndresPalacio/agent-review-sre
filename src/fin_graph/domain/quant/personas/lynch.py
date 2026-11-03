"""Peter Lynch-style growth-at-a-reasonable-price scoring."""

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


def analyze_lynch_growth(snapshot: CompanySnapshot) -> Component:
    return growth_component(snapshot, "lynch_growth")


def analyze_lynch_fundamentals(snapshot: CompanySnapshot) -> Component:
    health = health_component(snapshot)
    latest = snapshot.metrics[0] if snapshot.metrics else None
    roe = latest.return_on_equity if latest else None
    score = (health.score or 0) + (
        2 if roe is not None and roe > 0.15 else 1 if roe and roe > 0 else 0
    )
    return Component(
        name="lynch_fundamentals",
        score=score,
        max_score=6,
        details=f"{health.details}; ROE {roe}",
        metrics={**health.metrics, "return_on_equity": roe},
    )


def analyze_lynch_valuation(snapshot: CompanySnapshot) -> Component:
    return valuation_component(snapshot, "lynch_valuation")


def analyze_sentiment(snapshot: CompanySnapshot) -> Component:
    return sentiment_component(snapshot)


def analyze_insider_activity(snapshot: CompanySnapshot) -> Component:
    return insider_component(snapshot)


def score_lynch(snapshot: CompanySnapshot) -> QuantScore:
    if not snapshot.metrics and not snapshot.line_items:
        return no_data(Domain.FUNDAMENTALS, snapshot.ticker, "no GARP observations")
    components = [
        analyze_lynch_growth(snapshot),
        analyze_lynch_fundamentals(snapshot),
        analyze_lynch_valuation(snapshot),
        analyze_sentiment(snapshot),
        analyze_insider_activity(snapshot),
    ]
    total = component_total(components)
    gaps = []
    if not snapshot.news:
        gaps.append("company news unavailable")
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
