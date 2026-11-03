"""Cathie Wood-style disruption, innovation growth, and valuation scoring."""

from __future__ import annotations

from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.quant.common import Component, QuantScore, no_data, score_signal
from fin_graph.domain.quant.personas import component_total, midpoint_confidence
from fin_graph.domain.quant.personas._growth_common import (
    growth_component,
    line_values,
    valuation_component,
)
from fin_graph.domain.tools import Domain

MAX_SCORE = 16.0


def analyze_disruptive_potential(snapshot: CompanySnapshot) -> Component:
    research = line_values(snapshot.line_items, "research_and_development")
    revenue = line_values(snapshot.line_items, "revenue")
    intensity = research[0] / revenue[0] if research and revenue and revenue[0] > 0 else None
    latest = snapshot.metrics[0] if snapshot.metrics else None
    gross_margin = latest.gross_margin if latest else None
    score = 0.0
    if intensity is not None:
        score += 2 if intensity >= 0.08 else 1 if intensity >= 0.03 else 0
    if gross_margin is not None:
        score += 2 if gross_margin >= 0.4 else 1 if gross_margin >= 0.25 else 0
    return Component(
        name="disruptive_potential",
        score=score,
        max_score=4,
        details=f"R&D intensity {intensity}; gross margin {gross_margin}",
        metrics={"research_intensity": intensity, "gross_margin": gross_margin},
    )


def analyze_innovation_growth(snapshot: CompanySnapshot) -> Component:
    return growth_component(snapshot, "innovation_growth")


def analyze_cathie_wood_valuation(snapshot: CompanySnapshot) -> Component:
    return valuation_component(snapshot, "cathie_wood_valuation")


def score_wood(snapshot: CompanySnapshot) -> QuantScore:
    if not snapshot.metrics and not snapshot.line_items:
        return no_data(Domain.FUNDAMENTALS, snapshot.ticker, "no innovation observations")
    components = [
        analyze_innovation_growth(snapshot),
        analyze_disruptive_potential(snapshot),
        analyze_cathie_wood_valuation(snapshot),
    ]
    total = component_total(components)
    gaps = []
    if not snapshot.metrics:
        gaps.append("financial metrics unavailable")
    if not snapshot.line_items:
        gaps.append("annual line items unavailable")
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
