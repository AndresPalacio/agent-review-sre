"""Mohnish Pabrai-style downside, cash-yield and doubling-potential scoring."""

from __future__ import annotations

from collections.abc import Sequence

from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.market import LineItem
from fin_graph.domain.quant.common import Component, QuantScore, Signal, no_data
from fin_graph.domain.quant.personas import midpoint_confidence
from fin_graph.domain.tools import Domain

MAX_SCORE = 10.0


def _values(items: Sequence[LineItem], name: str) -> list[float]:
    return [value for item in items if (value := item.value(name)) is not None]


def analyze_downside_protection(items: Sequence[LineItem]) -> Component:
    if not items:
        return Component(
            name="downside_protection", score=0, max_score=10, details="Insufficient data"
        )
    latest = items[0]
    score = 0.0
    details: list[str] = []
    cash = latest.value("cash_and_equivalents")
    debt = latest.value("total_debt")
    if cash is not None and debt is not None:
        net_cash = cash - debt
        if net_cash > 0:
            score += 3
        details.append(f"Net cash {net_cash:,.0f}")
    current_assets = latest.value("current_assets")
    current_liabilities = latest.value("current_liabilities")
    if current_assets is not None and current_liabilities is not None and current_liabilities > 0:
        current_ratio = current_assets / current_liabilities
        score += 2 if current_ratio >= 2 else 1 if current_ratio >= 1.2 else 0
        details.append(f"Current ratio {current_ratio:.2f}")
    equity = latest.value("shareholders_equity")
    if equity is not None and equity > 0 and debt is not None:
        leverage = debt / equity
        score += 2 if leverage < 0.3 else 1 if leverage < 0.7 else 0
        details.append(f"D/E {leverage:.2f}")
    cash_flows = _values(items, "free_cash_flow")
    if len(cash_flows) >= 3:
        recent = sum(cash_flows[:3]) / 3
        older = sum(cash_flows[-3:]) / 3 if len(cash_flows) >= 6 else cash_flows[-1]
        score += 2 if recent > 0 and recent >= older else 1 if recent > 0 else 0
        details.append(f"Recent normalized FCF {recent:,.0f}")
    return Component(
        name="downside_protection",
        score=min(10.0, score),
        max_score=10,
        details="; ".join(details),
    )


def analyze_pabrai_valuation(items: Sequence[LineItem], market_cap: float | None) -> Component:
    cash_flows = _values(items, "free_cash_flow")
    if market_cap is None or len(cash_flows) < 3:
        return Component(
            name="valuation",
            score=0,
            max_score=10,
            details="Insufficient FCF history or market cap",
            metrics={"free_cash_flow_yield": None, "normalized_free_cash_flow": None},
        )
    sample = cash_flows[: min(5, len(cash_flows))]
    normalized = sum(sample) / len(sample)
    if normalized <= 0:
        return Component(
            name="valuation",
            score=0,
            max_score=10,
            details="Non-positive normalized FCF",
            metrics={"free_cash_flow_yield": None, "normalized_free_cash_flow": normalized},
        )
    cash_flow_yield = normalized / market_cap
    score = (
        4.0
        if cash_flow_yield > 0.10
        else 3.0
        if cash_flow_yield > 0.07
        else 2.0
        if cash_flow_yield > 0.05
        else 1.0
        if cash_flow_yield > 0.03
        else 0.0
    )
    capex_ratios = [
        abs(capex) / revenue
        for item in items
        if (capex := item.value("capital_expenditure")) is not None
        and (revenue := item.value("revenue")) is not None
        and revenue > 0
    ]
    average_capex = sum(capex_ratios) / len(capex_ratios) if capex_ratios else None
    if average_capex is not None:
        score += 2 if average_capex < 0.05 else 1 if average_capex < 0.10 else 0
    return Component(
        name="valuation",
        score=min(10.0, score),
        max_score=10,
        details=f"FCF yield {cash_flow_yield:.1%}; average capex intensity {average_capex}",
        metrics={
            "free_cash_flow_yield": cash_flow_yield,
            "normalized_free_cash_flow": normalized,
            "average_capex_to_revenue": average_capex,
        },
    )


def analyze_double_potential(items: Sequence[LineItem], market_cap: float | None) -> Component:
    if not items or market_cap is None:
        return Component(
            name="double_potential", score=0, max_score=10, details="Insufficient data"
        )
    score = 0.0
    details: list[str] = []
    revenues = _values(items, "revenue")
    if len(revenues) >= 3:
        recent = sum(revenues[:3]) / 3
        older = sum(revenues[-3:]) / 3 if len(revenues) >= 6 else revenues[-1]
        if older > 0:
            growth = recent / older - 1
            score += 2 if growth > 0.15 else 1 if growth > 0.05 else 0
            details.append(f"Revenue trajectory {growth:.1%}")
    cash_flows = _values(items, "free_cash_flow")
    if len(cash_flows) >= 3:
        recent = sum(cash_flows[:3]) / 3
        older = sum(cash_flows[-3:]) / 3 if len(cash_flows) >= 6 else cash_flows[-1]
        if older:
            growth = recent / older - 1
            score += 3 if growth > 0.20 else 2 if growth > 0.08 else 1 if growth > 0 else 0
            details.append(f"FCF trajectory {growth:.1%}")
    valuation = analyze_pabrai_valuation(items, market_cap)
    cash_flow_yield = valuation.metrics.get("free_cash_flow_yield")
    if isinstance(cash_flow_yield, int | float):
        score += 3 if cash_flow_yield > 0.08 else 1 if cash_flow_yield > 0.05 else 0
    return Component(
        name="double_potential",
        score=min(10.0, score),
        max_score=10,
        details="; ".join(details),
        metrics={"free_cash_flow_yield": cash_flow_yield},
    )


def score_pabrai(snapshot: CompanySnapshot) -> QuantScore:
    if not snapshot.line_items and not snapshot.metrics:
        return no_data(Domain.VALUATION, snapshot.ticker, "no financial observations")
    downside = analyze_downside_protection(snapshot.line_items)
    valuation = analyze_pabrai_valuation(snapshot.line_items, snapshot.market_cap)
    double = analyze_double_potential(snapshot.line_items, snapshot.market_cap)
    total = (
        (downside.score or 0) * 0.45 + (valuation.score or 0) * 0.35 + (double.score or 0) * 0.20
    )
    signal = Signal.BULLISH if total >= 7.5 else Signal.BEARISH if total <= 4 else Signal.NEUTRAL
    gaps = []
    if not snapshot.line_items:
        gaps.append("annual line items unavailable")
    if snapshot.market_cap is None:
        gaps.append("market cap unavailable")
    return QuantScore(
        analyst=Domain.VALUATION,
        ticker=snapshot.ticker,
        signal=signal,
        confidence=midpoint_confidence(total, MAX_SCORE),
        score=total,
        max_score=MAX_SCORE,
        components=[downside, valuation, double],
        data_gaps=gaps,
    )
