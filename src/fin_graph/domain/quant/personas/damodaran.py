"""Aswath Damodaran-style growth, risk, relative-value and FCFF scoring."""

from __future__ import annotations

from collections.abc import Sequence

from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.market import FinancialMetrics, LineItem
from fin_graph.domain.quant.common import Component, QuantScore, Signal, no_data
from fin_graph.domain.quant.personas import component_total, midpoint_confidence
from fin_graph.domain.tools import Domain

MAX_SCORE = 8.0


def _values(items: Sequence[LineItem], name: str) -> list[float]:
    return [value for item in items if (value := item.value(name)) is not None]


def analyze_growth_and_reinvestment(
    metrics: Sequence[FinancialMetrics], items: Sequence[LineItem]
) -> Component:
    if len(metrics) < 2:
        return Component(
            name="growth_and_reinvestment",
            score=0,
            max_score=4,
            details="Insufficient history",
        )
    score = 0.0
    details: list[str] = []
    revenues = [
        float(value) for metric in reversed(metrics) if (value := getattr(metric, "revenue", None))
    ]
    if len(revenues) >= 2 and revenues[0] > 0:
        cagr = (revenues[-1] / revenues[0]) ** (1 / (len(revenues) - 1)) - 1
        if cagr > 0.08:
            score += 2
        elif cagr > 0.03:
            score += 1
        details.append(f"Revenue CAGR {cagr:.1%}")
    else:
        cagr = None
        details.append("Revenue data incomplete")

    cash_flows = [value for value in reversed(_values(items, "free_cash_flow")) if value]
    if len(cash_flows) >= 2 and cash_flows[-1] > cash_flows[0]:
        score += 1
        details.append("Positive FCFF growth")
    else:
        details.append("Flat, declining, or unavailable FCFF")

    roic = metrics[0].return_on_invested_capital
    if roic and roic > 0.10:
        score += 1
        details.append(f"ROIC {roic:.1%}")
    return Component(
        name="growth_and_reinvestment",
        score=score,
        max_score=4,
        details="; ".join(details),
        metrics={"revenue_cagr": cagr, "roic": roic},
    )


def estimate_cost_of_equity(beta: float | None) -> float:
    return 0.04 + (1.0 if beta is None else beta) * 0.05


def analyze_risk_profile(
    metrics: Sequence[FinancialMetrics], items: Sequence[LineItem]
) -> Component:
    latest = metrics[0] if metrics else None
    score = 0.0
    beta = getattr(latest, "beta", None)
    if beta is not None and beta < 1.3:
        score += 1
    debt_to_equity = latest.debt_to_equity if latest else None
    if debt_to_equity is not None and debt_to_equity < 1:
        score += 1
    ebit = getattr(latest, "ebit", None) if latest else None
    interest = getattr(latest, "interest_expense", None) if latest else None
    coverage = ebit / abs(interest) if ebit and interest else None
    if coverage is not None and coverage > 3:
        score += 1
    return Component(
        name="risk_profile",
        score=score,
        max_score=3,
        details=f"Beta {beta}; D/E {debt_to_equity}; interest coverage {coverage}",
        metrics={
            "beta": beta,
            "debt_to_equity": debt_to_equity,
            "interest_coverage": coverage,
            "cost_of_equity": estimate_cost_of_equity(beta),
        },
    )


def analyze_relative_valuation(metrics: Sequence[FinancialMetrics]) -> Component:
    price_earnings = [
        metric.price_to_earnings_ratio
        for metric in metrics
        if metric.price_to_earnings_ratio is not None
    ]
    score = 0.0
    current = median = None
    if len(price_earnings) >= 5:
        current = price_earnings[0]
        median = sorted(price_earnings)[len(price_earnings) // 2]
        if current < 0.7 * median:
            score = 1
        elif current > 1.3 * median:
            score = -1
    return Component(
        name="relative_valuation",
        score=score,
        max_score=1,
        details="Insufficient P/E history"
        if median is None
        else f"P/E {current} vs median {median}",
        metrics={"price_to_earnings": current, "median_price_to_earnings": median},
    )


def calculate_intrinsic_value_dcf(
    metrics: Sequence[FinancialMetrics], items: Sequence[LineItem], cost_of_equity: float
) -> Component:
    if len(metrics) < 2 or not items:
        return Component(
            name="intrinsic_value",
            details="Insufficient data",
            metrics={"intrinsic_value": None, "intrinsic_per_share": None},
        )
    base_cash_flow = getattr(metrics[0], "free_cash_flow", None)
    shares = items[0].value("outstanding_shares")
    revenues = [
        float(value) for metric in reversed(metrics) if (value := getattr(metric, "revenue", None))
    ]
    if not base_cash_flow or not shares:
        return Component(
            name="intrinsic_value",
            details="Missing positive FCFF or share count",
            metrics={"intrinsic_value": None, "intrinsic_per_share": None},
        )
    if len(revenues) >= 2 and revenues[0] > 0:
        base_growth = min(
            (revenues[-1] / revenues[0]) ** (1 / (len(revenues) - 1)) - 1,
            0.12,
        )
    else:
        base_growth = 0.04
    terminal_growth = 0.025
    years = 10
    discount = cost_of_equity or 0.09
    present_value = 0.0
    growth = base_growth
    growth_step = (terminal_growth - base_growth) / (years - 1)
    for year in range(1, years + 1):
        projected = base_cash_flow * (1 + growth)
        present_value += projected / (1 + discount) ** year
        growth += growth_step
    terminal_value = (
        base_cash_flow
        * (1 + terminal_growth)
        / (discount - terminal_growth)
        / (1 + discount) ** years
    )
    intrinsic = present_value + terminal_value
    return Component(
        name="intrinsic_value",
        details="FCFF DCF completed",
        metrics={
            "intrinsic_value": intrinsic,
            "intrinsic_per_share": intrinsic / shares,
            "base_cash_flow": base_cash_flow,
            "base_growth": base_growth,
            "terminal_growth": terminal_growth,
            "discount_rate": discount,
        },
    )


def score_damodaran(snapshot: CompanySnapshot) -> QuantScore:
    if not snapshot.metrics and not snapshot.line_items:
        return no_data(Domain.VALUATION, snapshot.ticker, "no financial observations")
    growth = analyze_growth_and_reinvestment(snapshot.metrics, snapshot.line_items)
    risk = analyze_risk_profile(snapshot.metrics, snapshot.line_items)
    relative = analyze_relative_valuation(snapshot.metrics)
    intrinsic = calculate_intrinsic_value_dcf(
        snapshot.metrics,
        snapshot.line_items,
        float(risk.metrics["cost_of_equity"]),
    )
    components = [growth, risk, relative]
    total = component_total(components)
    intrinsic_value = intrinsic.metrics.get("intrinsic_value")
    margin = (
        (float(intrinsic_value) - snapshot.market_cap) / snapshot.market_cap
        if isinstance(intrinsic_value, int | float) and snapshot.market_cap
        else None
    )
    if margin is not None and margin >= 0.25:
        signal = Signal.BULLISH
    elif margin is not None and margin <= -0.25:
        signal = Signal.BEARISH
    else:
        signal = Signal.NEUTRAL
    gaps: list[str] = []
    if not snapshot.metrics:
        gaps.append("financial metrics unavailable")
    if not snapshot.line_items:
        gaps.append("annual line items unavailable")
    if snapshot.market_cap is None:
        gaps.append("market cap unavailable")
    if intrinsic_value is None:
        gaps.append("intrinsic value unavailable")
    intrinsic = intrinsic.model_copy(
        update={
            "metrics": {
                **intrinsic.metrics,
                "market_cap": snapshot.market_cap,
                "margin_of_safety": margin,
            }
        }
    )
    return QuantScore(
        analyst=Domain.VALUATION,
        ticker=snapshot.ticker,
        signal=signal,
        confidence=(
            min(100.0, round(abs(margin) * 100, 2))
            if margin is not None
            else midpoint_confidence(total, MAX_SCORE)
        ),
        score=total,
        max_score=MAX_SCORE,
        components=[*components, intrinsic],
        data_gaps=gaps,
    )
