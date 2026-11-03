"""Charlie Munger-style quality, predictability and owner-earnings valuation."""

from __future__ import annotations

from collections.abc import Sequence

from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.market import LineItem
from fin_graph.domain.quant.buffett import (
    book_value_growth,
    consistency,
    management,
    moat,
    pricing_power,
)
from fin_graph.domain.quant.common import Component, QuantScore, Signal, no_data
from fin_graph.domain.quant.personas import normalized_component
from fin_graph.domain.tools import Domain

MAX_SCORE = 10.0


def _values(items: Sequence[LineItem], name: str) -> list[float]:
    return [value for item in items if (value := item.value(name)) is not None]


def analyze_moat_strength(snapshot: CompanySnapshot) -> Component:
    return normalized_component(
        "moat_strength",
        (moat(snapshot.metrics), pricing_power(snapshot.line_items, snapshot.metrics)),
    )


def analyze_management_quality(snapshot: CompanySnapshot) -> Component:
    base = normalized_component("management_quality", (management(snapshot.line_items),))
    bought = sum(
        trade.transaction_shares or 0
        for trade in snapshot.insider_trades
        if (trade.transaction_shares or 0) > 0
    )
    sold = abs(
        sum(
            trade.transaction_shares or 0
            for trade in snapshot.insider_trades
            if (trade.transaction_shares or 0) < 0
        )
    )
    ratio = bought / (bought + sold) if bought + sold > 0 else None
    return base.model_copy(update={"metrics": {**base.metrics, "insider_buy_ratio": ratio}})


def analyze_predictability(snapshot: CompanySnapshot) -> Component:
    return normalized_component(
        "predictability",
        (consistency(snapshot.line_items), book_value_growth(snapshot.line_items)),
    )


def calculate_munger_valuation(items: Sequence[LineItem], market_cap: float | None) -> Component:
    cash_flows = _values(items, "free_cash_flow")
    if market_cap is None or len(cash_flows) < 3:
        return Component(
            name="valuation",
            score=0,
            max_score=10,
            details="Insufficient free cash flow history or market cap",
            metrics={
                "intrinsic_value_range": {},
                "free_cash_flow_yield": None,
                "normalized_free_cash_flow": None,
                "margin_of_safety": None,
            },
        )
    sample = cash_flows[: min(5, len(cash_flows))]
    normalized = sum(sample) / len(sample)
    if normalized <= 0:
        return Component(
            name="valuation",
            score=0,
            max_score=10,
            details="Non-positive normalized free cash flow",
            metrics={
                "intrinsic_value_range": {},
                "free_cash_flow_yield": None,
                "normalized_free_cash_flow": normalized,
                "margin_of_safety": None,
            },
        )
    cash_flow_yield = normalized / market_cap
    score = (
        4.0
        if cash_flow_yield > 0.08
        else 3.0
        if cash_flow_yield > 0.05
        else 1.0
        if cash_flow_yield > 0.03
        else 0.0
    )
    conservative = normalized * 10
    reasonable = normalized * 15
    optimistic = normalized * 20
    margin = (reasonable - market_cap) / market_cap
    score += 3 if margin > 0.3 else 2 if margin > 0.1 else 1 if margin > -0.1 else 0
    recent = sum(cash_flows[:3]) / 3
    older = sum(cash_flows[-3:]) / 3 if len(cash_flows) >= 6 else cash_flows[-1]
    score += 3 if recent > older * 1.2 else 2 if recent > older else 0
    return Component(
        name="valuation",
        score=min(10.0, score),
        max_score=10,
        details=f"FCF yield {cash_flow_yield:.1%}; margin to reasonable value {margin:.1%}",
        metrics={
            "intrinsic_value_range": {
                "conservative": conservative,
                "reasonable": reasonable,
                "optimistic": optimistic,
            },
            "free_cash_flow_yield": cash_flow_yield,
            "normalized_free_cash_flow": normalized,
            "margin_of_safety": margin,
        },
    )


def _confidence(components: Sequence[Component], signal: Signal) -> float:
    moat_score, management_score, predictability_score, valuation_score = (
        float(component.score or 0) for component in components
    )
    quality = 0.35 * moat_score + 0.25 * management_score + 0.25 * predictability_score
    quality_pct = 100 * quality / 8.5 if quality > 0 else 0.0
    margin = components[-1].metrics.get("margin_of_safety")
    margin = float(margin) if isinstance(margin, int | float) else 0.0
    adjustment = max(-10.0, min(10.0, margin * 100 / 3))
    base = 0.85 * quality_pct + 0.15 * (valuation_score * 10) + adjustment
    if signal is Signal.BULLISH:
        lower, upper = (50 if quality_pct >= 55 else 30), (100 if margin > 0 else 69)
    elif signal is Signal.BEARISH:
        lower, upper = (10 if margin < -0.05 else 30), 49
    else:
        lower, upper = 50, 69
    return float(max(10, min(100, round(max(lower, min(upper, base))))))


def score_munger(snapshot: CompanySnapshot) -> QuantScore:
    if not snapshot.metrics and not snapshot.line_items:
        return no_data(Domain.FUNDAMENTALS, snapshot.ticker, "no financial observations")
    components = [
        analyze_moat_strength(snapshot),
        analyze_management_quality(snapshot),
        analyze_predictability(snapshot),
        calculate_munger_valuation(snapshot.line_items, snapshot.market_cap),
    ]
    total = (
        (components[0].score or 0) * 0.35
        + (components[1].score or 0) * 0.25
        + (components[2].score or 0) * 0.25
        + (components[3].score or 0) * 0.15
    )
    signal = Signal.BULLISH if total >= 7.5 else Signal.BEARISH if total <= 5.5 else Signal.NEUTRAL
    gaps = []
    if not snapshot.metrics:
        gaps.append("financial metrics unavailable")
    if not snapshot.line_items:
        gaps.append("annual line items unavailable")
    if snapshot.market_cap is None:
        gaps.append("market cap unavailable")
    if not snapshot.insider_trades:
        gaps.append("insider trades unavailable")
    return QuantScore(
        analyst=Domain.FUNDAMENTALS,
        ticker=snapshot.ticker,
        signal=signal,
        confidence=_confidence(components, signal),
        score=total,
        max_score=MAX_SCORE,
        components=components,
        data_gaps=gaps,
    )
