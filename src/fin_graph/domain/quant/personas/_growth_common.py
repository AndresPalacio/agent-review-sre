"""Pure components shared by growth, macro, and sentiment personas."""

from __future__ import annotations

from collections.abc import Sequence

from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.market import LineItem
from fin_graph.domain.quant.common import Component


def line_values(items: Sequence[LineItem], name: str) -> list[float]:
    return [value for item in items if (value := item.value(name)) is not None]


def growth_component(snapshot: CompanySnapshot, name: str = "growth_trends") -> Component:
    latest = snapshot.metrics[0] if snapshot.metrics else None
    values = {
        "revenue_growth": latest.revenue_growth if latest else None,
        "earnings_growth": latest.earnings_growth if latest else None,
        "free_cash_flow_growth": latest.free_cash_flow_growth if latest else None,
        "operating_income_growth": latest.operating_income_growth if latest else None,
    }
    score = 0.0
    for value in values.values():
        if value is None:
            continue
        score += 2 if value > 0.10 else 1 if value > 0 else -1 if value < 0 else 0
    return Component(
        name=name,
        score=score,
        max_score=8,
        details="; ".join(f"{key}={value}" for key, value in values.items()),
        metrics=values,
    )


def health_component(snapshot: CompanySnapshot, name: str = "financial_health") -> Component:
    latest = snapshot.metrics[0] if snapshot.metrics else None
    debt_to_equity = latest.debt_to_equity if latest else None
    current_ratio = latest.current_ratio if latest else None
    score = 0.0
    if debt_to_equity is not None:
        score += 2 if debt_to_equity < 0.5 else 1 if debt_to_equity < 1 else 0
    if current_ratio is not None:
        score += 2 if current_ratio >= 1.5 else 1 if current_ratio >= 1 else 0
    return Component(
        name=name,
        score=score,
        max_score=4,
        details=f"D/E {debt_to_equity}; current ratio {current_ratio}",
        metrics={"debt_to_equity": debt_to_equity, "current_ratio": current_ratio},
    )


def insider_component(snapshot: CompanySnapshot, name: str = "insider_activity") -> Component:
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
    net = bought - sold
    score = 2.0 if net > 0 else -1.0 if net < 0 else 0.0
    return Component(
        name=name,
        score=score,
        max_score=2,
        details=f"Net insider shares {net:,.0f}",
        metrics={"shares_bought": bought, "shares_sold": sold, "net_shares": net},
    )


def sentiment_component(snapshot: CompanySnapshot, name: str = "sentiment") -> Component:
    positive = sum(item.sentiment == "positive" for item in snapshot.news)
    negative = sum(item.sentiment == "negative" for item in snapshot.news)
    total = positive + negative
    score = 0.0 if total == 0 else 2 * (positive - negative) / total
    return Component(
        name=name,
        score=score,
        max_score=2,
        details=f"Positive {positive}; negative {negative}",
        metrics={"positive": positive, "negative": negative},
    )


def momentum_component(snapshot: CompanySnapshot, name: str = "price_momentum") -> Component:
    closes = [price.close for price in snapshot.prices]
    if len(closes) < 20 or closes[0] <= 0:
        return Component(
            name=name,
            score=0,
            max_score=3,
            details="Insufficient price history",
            metrics={"return": None},
        )
    price_return = closes[-1] / closes[0] - 1
    score = (
        3.0
        if price_return > 0.20
        else 2.0
        if price_return > 0.05
        else 1.0
        if price_return > 0
        else 0.0
    )
    return Component(
        name=name,
        score=score,
        max_score=3,
        details=f"Price return {price_return:.1%}",
        metrics={"return": price_return},
    )


def valuation_component(snapshot: CompanySnapshot, name: str = "valuation") -> Component:
    latest = snapshot.metrics[0] if snapshot.metrics else None
    peg = latest.peg_ratio if latest else None
    score = 2.0 if peg is not None and 0 < peg <= 1 else 1.0 if peg is not None and peg < 2 else 0.0
    cash_flows = line_values(snapshot.line_items, "free_cash_flow")
    cash_flow_yield = (
        cash_flows[0] / snapshot.market_cap
        if cash_flows and snapshot.market_cap is not None
        else None
    )
    if cash_flow_yield is not None and cash_flow_yield > 0.08:
        score += 2
    elif cash_flow_yield is not None and cash_flow_yield > 0.04:
        score += 1
    return Component(
        name=name,
        score=score,
        max_score=4,
        details=f"PEG {peg}; FCF yield {cash_flow_yield}",
        metrics={"peg_ratio": peg, "free_cash_flow_yield": cash_flow_yield},
    )
