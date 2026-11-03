"""Bill Ackman-style business quality, discipline, activism and valuation scoring."""

from __future__ import annotations

from collections.abc import Sequence

from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.market import FinancialMetrics, LineItem
from fin_graph.domain.quant.common import Component, QuantScore, no_data, score_signal
from fin_graph.domain.quant.personas import component_total, midpoint_confidence
from fin_graph.domain.tools import Domain

MAX_SCORE = 20.0


def _values(items: Sequence[LineItem], name: str) -> list[float]:
    return [value for item in items if (value := item.value(name)) is not None]


def analyze_business_quality(
    metrics: Sequence[FinancialMetrics], items: Sequence[LineItem]
) -> Component:
    score = 0.0
    details: list[str] = []
    revenues = _values(items, "revenue")
    if len(revenues) >= 2 and revenues[-1]:
        growth = (revenues[0] - revenues[-1]) / abs(revenues[-1])
        score += 2 if growth > 0.5 else 1 if growth > 0 else 0
        details.append(f"Cumulative revenue growth {growth:.1%}")
    margins = _values(items, "operating_margin")
    if margins and sum(margin > 0.15 for margin in margins) >= len(margins) // 2 + 1:
        score += 2
        details.append("Operating margins often exceed 15%")
    cash_flows = _values(items, "free_cash_flow")
    if cash_flows and sum(value > 0 for value in cash_flows) >= len(cash_flows) // 2 + 1:
        score += 1
        details.append("Most periods have positive free cash flow")
    roe = metrics[0].return_on_equity if metrics else None
    if roe is not None and roe > 0.15:
        score += 2
        details.append(f"ROE {roe:.1%}")
    return Component(
        name="business_quality",
        score=score,
        max_score=7,
        details="; ".join(details) or "Insufficient business-quality data",
        metrics={"return_on_equity": roe},
    )


def analyze_financial_discipline(
    metrics: Sequence[FinancialMetrics], items: Sequence[LineItem]
) -> Component:
    if not metrics or not items:
        return Component(
            name="financial_discipline",
            score=0,
            max_score=4,
            details="Insufficient data to analyze financial discipline",
        )
    score = 0.0
    details: list[str] = []
    debt_to_equity = _values(items, "debt_to_equity")
    if debt_to_equity:
        if sum(value < 1 for value in debt_to_equity) >= len(debt_to_equity) // 2 + 1:
            score += 2
            details.append("Debt-to-equity below one in most periods")
    else:
        ratios = [
            liabilities / assets
            for item in items
            if (liabilities := item.value("total_liabilities")) is not None
            and (assets := item.value("total_assets")) is not None
            and assets > 0
        ]
        if ratios and sum(ratio < 0.5 for ratio in ratios) >= len(ratios) // 2 + 1:
            score += 2
            details.append("Liabilities below half of assets in most periods")
    dividends = _values(items, "dividends_and_other_cash_distributions")
    if dividends and sum(value < 0 for value in dividends) >= len(dividends) // 2 + 1:
        score += 1
        details.append("Consistent shareholder distributions")
    shares = _values(items, "outstanding_shares")
    if len(shares) >= 2 and shares[0] < shares[-1]:
        score += 1
        details.append("Outstanding shares decreased")
    return Component(
        name="financial_discipline",
        score=score,
        max_score=4,
        details="; ".join(details) or "No evidence of financial discipline",
    )


def analyze_activism_potential(items: Sequence[LineItem]) -> Component:
    revenues = _values(items, "revenue")
    margins = _values(items, "operating_margin")
    growth = (
        (revenues[0] - revenues[-1]) / abs(revenues[-1])
        if len(revenues) >= 2 and revenues[-1]
        else None
    )
    average_margin = sum(margins) / len(margins) if margins else None
    score = (
        2.0
        if growth is not None
        and growth > 0.15
        and average_margin is not None
        and average_margin < 0.1
        else 0.0
    )
    return Component(
        name="activism_potential",
        score=score,
        max_score=2,
        details="Operational improvement opportunity" if score else "No clear activism opportunity",
        metrics={"revenue_growth": growth, "average_operating_margin": average_margin},
    )


def analyze_valuation(items: Sequence[LineItem], market_cap: float | None) -> Component:
    cash_flow = items[0].value("free_cash_flow") if items else None
    if cash_flow is None or cash_flow <= 0 or market_cap is None or market_cap <= 0:
        return Component(
            name="valuation",
            score=0,
            max_score=3,
            details="Positive FCF and market cap are required",
            metrics={"intrinsic_value": None, "margin_of_safety": None},
        )
    growth = 0.06
    discount = 0.10
    years = 5
    present = sum(
        cash_flow * (1 + growth) ** year / (1 + discount) ** year for year in range(1, years + 1)
    )
    terminal = cash_flow * (1 + growth) ** years * 15 / (1 + discount) ** years
    intrinsic = present + terminal
    margin = (intrinsic - market_cap) / market_cap
    score = 3.0 if margin > 0.3 else 1.0 if margin > 0.1 else 0.0
    return Component(
        name="valuation",
        score=score,
        max_score=3,
        details=f"Intrinsic value {intrinsic:,.2f}; margin of safety {margin:.2%}",
        metrics={"intrinsic_value": intrinsic, "margin_of_safety": margin},
    )


def score_ackman(snapshot: CompanySnapshot) -> QuantScore:
    if not snapshot.metrics and not snapshot.line_items:
        return no_data(Domain.FUNDAMENTALS, snapshot.ticker, "no financial observations")
    components = [
        analyze_business_quality(snapshot.metrics, snapshot.line_items),
        analyze_financial_discipline(snapshot.metrics, snapshot.line_items),
        analyze_activism_potential(snapshot.line_items),
        analyze_valuation(snapshot.line_items, snapshot.market_cap),
    ]
    total = component_total(components)
    gaps = []
    if not snapshot.metrics:
        gaps.append("financial metrics unavailable")
    if not snapshot.line_items:
        gaps.append("annual line items unavailable")
    if snapshot.market_cap is None:
        gaps.append("market cap unavailable")
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
