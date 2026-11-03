"""Charlie Munger scorer ported from ai-hedge-fund's deterministic pre-analysis."""

from __future__ import annotations

from collections.abc import Sequence

from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.market import LineItem
from fin_graph.domain.quant.common import Component, QuantScore, Signal, no_data
from fin_graph.domain.tools import Domain

MAX_SCORE = 10.0


def _values(items: Sequence[LineItem], name: str) -> list[float]:
    return [value for item in items if (value := item.value(name)) is not None]


def _component(name: str, score: float, details: list[str], **metrics: object) -> Component:
    return Component(
        name=name,
        score=max(0.0, min(10.0, score)),
        max_score=10,
        details="; ".join(details),
        metrics=metrics,
    )


def analyze_moat_strength(snapshot: CompanySnapshot) -> Component:
    """Port of ``analyze_moat_strength`` (raw maximum 9, scaled to ten)."""
    if not snapshot.metrics or not snapshot.line_items:
        return _component("moat_strength", 0, ["Insufficient data to analyze moat strength"])

    items = snapshot.line_items
    raw = 0
    details: list[str] = []
    roic = _values(items, "return_on_invested_capital")
    if roic:
        high = sum(value > 0.15 for value in roic)
        if high >= len(roic) * 0.8:
            raw += 3
        elif high >= len(roic) * 0.5:
            raw += 2
        elif high > 0:
            raw += 1
        details.append(f"ROIC above 15% in {high}/{len(roic)} periods")
    else:
        details.append("No ROIC data available")

    margins = _values(items, "gross_margin")
    if len(margins) >= 3:
        improving = sum(margins[index] >= margins[index - 1] for index in range(1, len(margins)))
        if improving >= len(margins) * 0.7:
            raw += 2
        elif sum(margins) / len(margins) > 0.3:
            raw += 1
        details.append(f"Average gross margin {sum(margins) / len(margins):.1%}")
    else:
        details.append("Insufficient gross margin data")

    capex_ratios = [
        abs(capex) / revenue
        for item in items
        if (capex := item.value("capital_expenditure")) is not None
        and (revenue := item.value("revenue")) is not None
        and revenue > 0
    ]
    if len(items) >= 3 and capex_ratios:
        average_capex = sum(capex_ratios) / len(capex_ratios)
        raw += 2 if average_capex < 0.05 else 1 if average_capex < 0.10 else 0
        details.append(f"Average capex {average_capex:.1%} of revenue")
    else:
        average_capex = None
        details.append("Insufficient capital intensity data")

    research = _values(items, "research_and_development")
    intangibles = _values(items, "goodwill_and_intangible_assets")
    if research and sum(research) > 0:
        raw += 1
        details.append("Invests in R&D")
    if intangibles:
        raw += 1
        details.append("Goodwill or intangible assets present")

    return _component(
        "moat_strength",
        raw * 10 / 9,
        details,
        high_roic_periods=sum(value > 0.15 for value in roic),
        roic_periods=len(roic),
        average_capex_to_revenue=average_capex,
    )


def analyze_management_quality(snapshot: CompanySnapshot) -> Component:
    """Port of the five original management-quality tests (raw maximum 12)."""
    items = snapshot.line_items
    if not items:
        return _component("management_quality", 0, ["Insufficient data to analyze management"])

    raw = 0
    details: list[str] = []
    free_cash_flow = _values(items, "free_cash_flow")
    net_income = _values(items, "net_income")
    conversion: float | None = None
    if free_cash_flow and net_income and len(free_cash_flow) == len(net_income):
        ratios = [
            cash / income
            for cash, income in zip(free_cash_flow, net_income, strict=True)
            if income > 0
        ]
        if ratios:
            conversion = sum(ratios) / len(ratios)
            raw += (
                3 if conversion > 1.1 else 2 if conversion > 0.9 else 1 if conversion > 0.7 else 0
            )
            details.append(f"FCF/net income {conversion:.2f}")
    if conversion is None:
        details.append("Missing FCF or net income data")

    debts = _values(items, "total_debt")
    equities = _values(items, "shareholders_equity")
    debt_to_equity: float | None = None
    if debts and equities and len(debts) == len(equities):
        debt_to_equity = debts[0] / equities[0] if equities[0] > 0 else float("inf")
        raw += (
            3
            if debt_to_equity < 0.3
            else 2
            if debt_to_equity < 0.7
            else 1
            if debt_to_equity < 1.5
            else 0
        )
        details.append(f"Debt/equity {debt_to_equity:.2f}")
    else:
        details.append("Missing debt or equity data")

    cash = _values(items, "cash_and_equivalents")
    revenues = _values(items, "revenue")
    cash_to_revenue: float | None = None
    if cash and revenues:
        cash_to_revenue = cash[0] / revenues[0] if revenues[0] > 0 else 0
        if 0.1 <= cash_to_revenue <= 0.25:
            raw += 2
        elif 0.05 <= cash_to_revenue < 0.1 or 0.25 < cash_to_revenue <= 0.4:
            raw += 1
        details.append(f"Cash/revenue {cash_to_revenue:.2f}")
    else:
        details.append("Insufficient cash or revenue data")

    buys = sum((trade.transaction_shares or 0) > 0 for trade in snapshot.insider_trades)
    sells = sum((trade.transaction_shares or 0) < 0 for trade in snapshot.insider_trades)
    trades = buys + sells
    insider_buy_ratio = buys / trades if trades else None
    if insider_buy_ratio is not None:
        if insider_buy_ratio > 0.7:
            raw += 2
        elif insider_buy_ratio > 0.4:
            raw += 1
        elif insider_buy_ratio < 0.1 and sells > 5:
            raw -= 1
        details.append(f"Insider purchases {buys}/{trades}")
    else:
        details.append("No insider trading data available")

    shares = _values(items, "outstanding_shares")
    share_trend = "unknown"
    if len(shares) >= 3:
        if shares[0] < shares[-1] * 0.95:
            raw += 2
            share_trend = "decreasing"
        elif shares[0] < shares[-1] * 1.05:
            raw += 1
            share_trend = "stable"
        elif shares[0] > shares[-1] * 1.2:
            raw -= 1
            share_trend = "increasing"
        else:
            share_trend = "increasing"
        details.append(f"Share count {share_trend}")
    else:
        details.append("Insufficient share count data")

    return _component(
        "management_quality",
        raw * 10 / 12,
        details,
        fcf_to_net_income=conversion,
        insider_buy_ratio=insider_buy_ratio,
        recent_de_ratio=debt_to_equity,
        cash_to_revenue=cash_to_revenue,
        share_count_trend=share_trend,
    )


def analyze_predictability(items: Sequence[LineItem]) -> Component:
    """Port of the original revenue, profit, margin and cash reliability tests."""
    if len(items) < 5:
        return _component("predictability", 0, ["Need at least five periods"])

    raw = 0
    details: list[str] = []
    revenues = _values(items, "revenue")
    growth_rates: list[float] = []
    if len(revenues) >= 5:
        growth_rates = [
            revenues[index] / revenues[index + 1] - 1
            for index in range(len(revenues) - 1)
            if revenues[index + 1] != 0
        ]
    average_growth: float | None = None
    growth_volatility: float | None = None
    if growth_rates:
        average_growth = sum(growth_rates) / len(growth_rates)
        growth_volatility = sum(abs(value - average_growth) for value in growth_rates) / len(
            growth_rates
        )
        if average_growth > 0.05 and growth_volatility < 0.1:
            raw += 3
        elif average_growth > 0 and growth_volatility < 0.2:
            raw += 2
        elif average_growth > 0:
            raw += 1
        details.append(f"Revenue growth {average_growth:.1%}, volatility {growth_volatility:.1%}")
    else:
        details.append("Insufficient revenue history")

    operating_income = _values(items, "operating_income")
    if len(operating_income) >= 5:
        positive = sum(value > 0 for value in operating_income)
        raw += (
            3
            if positive == len(operating_income)
            else 2
            if positive >= len(operating_income) * 0.8
            else 1
            if positive >= len(operating_income) * 0.6
            else 0
        )
        details.append(f"Positive operating income {positive}/{len(operating_income)}")
    else:
        details.append("Insufficient operating income history")

    margins = _values(items, "operating_margin")
    margin_volatility: float | None = None
    if len(margins) >= 5:
        average_margin = sum(margins) / len(margins)
        margin_volatility = sum(abs(value - average_margin) for value in margins) / len(margins)
        raw += 2 if margin_volatility < 0.03 else 1 if margin_volatility < 0.07 else 0
        details.append(f"Operating-margin volatility {margin_volatility:.1%}")
    else:
        details.append("Insufficient margin history")

    free_cash_flow = _values(items, "free_cash_flow")
    if len(free_cash_flow) >= 5:
        positive_fcf = sum(value > 0 for value in free_cash_flow)
        raw += (
            2
            if positive_fcf == len(free_cash_flow)
            else 1
            if positive_fcf >= len(free_cash_flow) * 0.8
            else 0
        )
        details.append(f"Positive FCF {positive_fcf}/{len(free_cash_flow)}")
    else:
        details.append("Insufficient free cash flow history")

    return _component(
        "predictability",
        raw,
        details,
        average_revenue_growth=average_growth,
        revenue_growth_volatility=growth_volatility,
        operating_margin_volatility=margin_volatility,
    )


def calculate_munger_valuation(items: Sequence[LineItem], market_cap: float | None) -> Component:
    """Port of Munger's normalized FCF multiple and margin-of-safety score."""
    cash_flows = _values(items, "free_cash_flow")
    if market_cap is None or len(cash_flows) < 3:
        return _component("valuation", 0, ["Insufficient FCF history or market cap"])
    sample = cash_flows[: min(5, len(cash_flows))]
    normalized = sum(sample) / len(sample)
    if normalized <= 0 or market_cap <= 0:
        return _component(
            "valuation",
            0,
            ["Non-positive normalized FCF or market cap"],
            normalized_free_cash_flow=normalized,
        )

    cash_flow_yield = normalized / market_cap
    score = (
        4
        if cash_flow_yield > 0.08
        else 3
        if cash_flow_yield > 0.05
        else 1
        if cash_flow_yield > 0.03
        else 0
    )
    conservative, reasonable, optimistic = normalized * 10, normalized * 15, normalized * 20
    margin = (reasonable - market_cap) / market_cap
    score += 3 if margin > 0.3 else 2 if margin > 0.1 else 1 if margin > -0.1 else 0
    recent = sum(cash_flows[:3]) / 3
    older = sum(cash_flows[-3:]) / 3 if len(cash_flows) >= 6 else cash_flows[-1]
    score += 3 if recent > older * 1.2 else 2 if recent > older else 0
    return _component(
        "valuation",
        score,
        [f"FCF yield {cash_flow_yield:.1%}", f"Margin to reasonable value {margin:.1%}"],
        intrinsic_value_range={
            "conservative": conservative,
            "reasonable": reasonable,
            "optimistic": optimistic,
        },
        free_cash_flow_yield=cash_flow_yield,
        normalized_free_cash_flow=normalized,
        margin_of_safety=margin,
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
        analyze_predictability(snapshot.line_items),
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
