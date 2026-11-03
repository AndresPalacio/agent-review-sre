"""Rakesh Jhunjhunwala-style profitability, growth and balance-sheet scoring."""

from __future__ import annotations

from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.quant.common import Component, QuantScore, no_data, score_signal
from fin_graph.domain.quant.personas import component_total, midpoint_confidence
from fin_graph.domain.quant.personas._growth_common import (
    growth_component,
    health_component,
    insider_component,
    line_values,
)
from fin_graph.domain.tools import Domain

MAX_SCORE = 20.0


def analyze_profitability(snapshot: CompanySnapshot) -> Component:
    latest = snapshot.metrics[0] if snapshot.metrics else None
    roe = latest.return_on_equity if latest else None
    margin = latest.net_margin if latest else None
    score = (2 if roe is not None and roe > 0.15 else 1 if roe and roe > 0 else 0) + (
        2 if margin is not None and margin > 0.15 else 1 if margin and margin > 0 else 0
    )
    return Component(
        name="profitability",
        score=score,
        max_score=4,
        details=f"ROE {roe}; net margin {margin}",
        metrics={"return_on_equity": roe, "net_margin": margin},
    )


def analyze_growth(snapshot: CompanySnapshot) -> Component:
    base = growth_component(snapshot, "growth")
    return base.model_copy(update={"score": (base.score or 0) / 2, "max_score": 4})


def analyze_balance_sheet(snapshot: CompanySnapshot) -> Component:
    return health_component(snapshot, "balance_sheet")


def analyze_cash_flow(snapshot: CompanySnapshot) -> Component:
    values = line_values(snapshot.line_items, "free_cash_flow")
    positive = sum(value > 0 for value in values)
    score = 4.0 if values and positive == len(values) else 2.0 if positive else 0.0
    return Component(
        name="cash_flow",
        score=score,
        max_score=4,
        details=f"Positive FCF periods {positive}/{len(values)}",
        metrics={"positive_periods": positive, "periods": len(values)},
    )


def analyze_management_actions(snapshot: CompanySnapshot) -> Component:
    return insider_component(snapshot, "management_actions")


def assess_quality_metrics(snapshot: CompanySnapshot) -> float:
    return component_total(
        (
            analyze_profitability(snapshot),
            analyze_growth(snapshot),
            analyze_balance_sheet(snapshot),
            analyze_cash_flow(snapshot),
            analyze_management_actions(snapshot),
        )
    )


def score_jhunjhunwala(snapshot: CompanySnapshot) -> QuantScore:
    if not snapshot.metrics and not snapshot.line_items:
        return no_data(Domain.FUNDAMENTALS, snapshot.ticker, "no quality observations")
    components = [
        analyze_profitability(snapshot),
        analyze_growth(snapshot),
        analyze_balance_sheet(snapshot),
        analyze_cash_flow(snapshot),
        analyze_management_actions(snapshot),
    ]
    total = component_total(components)
    gaps = [] if snapshot.insider_trades else ["insider trades unavailable"]
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
