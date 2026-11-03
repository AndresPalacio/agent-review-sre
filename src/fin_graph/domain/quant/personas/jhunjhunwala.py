"""Rakesh Jhunjhunwala v1 profitability, quality and earnings-power valuation."""

from __future__ import annotations

from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.quant.common import Component, QuantScore, Signal, no_data
from fin_graph.domain.quant.personas._growth_common import line_values
from fin_graph.domain.tools import Domain

MAX_SCORE = 24.0


def _cagr_percent(values: list[float]) -> float | None:
    if len(values) < 3 or values[-1] <= 0:
        return None
    return float(((values[0] / values[-1]) ** (1 / (len(values) - 1)) - 1) * 100)


def analyze_profitability(snapshot: CompanySnapshot) -> Component:
    income = [value for value in line_values(snapshot.line_items, "net_income") if value > 0]
    assets = line_values(snapshot.line_items, "total_assets")
    liabilities = line_values(snapshot.line_items, "total_liabilities")
    operating = line_values(snapshot.line_items, "operating_income")
    revenue = line_values(snapshot.line_items, "revenue")
    eps = [value for value in line_values(snapshot.line_items, "earnings_per_share") if value > 0]
    raw = 0.0
    equity = assets[0] - liabilities[0] if assets and liabilities else None
    roe = income[0] / equity * 100 if income and equity and equity > 0 else None
    if roe is not None:
        raw += 3 if roe > 20 else 2 if roe > 15 else 1 if roe > 10 else 0
    margin = operating[0] / revenue[0] * 100 if operating and revenue and revenue[0] > 0 else None
    if margin is not None:
        raw += 2 if margin > 20 else 1 if margin > 15 else 0
    eps_cagr = _cagr_percent(eps)
    if eps_cagr is not None:
        raw += 3 if eps_cagr > 20 else 2 if eps_cagr > 15 else 1 if eps_cagr > 10 else 0
    return Component(
        name="profitability",
        score=raw,
        max_score=8,
        metrics={
            "return_on_equity_pct": roe,
            "operating_margin_pct": margin,
            "eps_cagr_pct": eps_cagr,
        },
    )


def analyze_growth(snapshot: CompanySnapshot) -> Component:
    revenue = [value for value in line_values(snapshot.line_items, "revenue") if value > 0]
    income = [value for value in line_values(snapshot.line_items, "net_income") if value > 0]
    revenue_cagr, income_cagr = _cagr_percent(revenue), _cagr_percent(income)
    raw = 0.0
    if revenue_cagr is not None:
        raw += 3 if revenue_cagr > 20 else 2 if revenue_cagr > 15 else 1 if revenue_cagr > 10 else 0
    if income_cagr is not None:
        raw += 3 if income_cagr > 25 else 2 if income_cagr > 20 else 1 if income_cagr > 15 else 0
    consistency: float | None = None
    if len(revenue) >= 3:
        declining = sum(revenue[index - 1] > revenue[index] for index in range(1, len(revenue)))
        consistency = 1 - declining / (len(revenue) - 1)
        raw += 1 if consistency >= 0.8 else 0
    return Component(
        name="growth",
        score=raw,
        max_score=7,
        metrics={
            "revenue_cagr_pct": revenue_cagr,
            "income_cagr_pct": income_cagr,
            "consistency_ratio": consistency,
        },
    )


def analyze_balance_sheet(snapshot: CompanySnapshot) -> Component:
    assets = line_values(snapshot.line_items, "total_assets")
    liabilities = line_values(snapshot.line_items, "total_liabilities")
    current_assets = line_values(snapshot.line_items, "current_assets")
    current_liabilities = line_values(snapshot.line_items, "current_liabilities")
    debt_ratio = liabilities[0] / assets[0] if assets and liabilities and assets[0] > 0 else None
    current_ratio = (
        current_assets[0] / current_liabilities[0]
        if current_assets and current_liabilities and current_liabilities[0] > 0
        else None
    )
    score = 0.0
    if debt_ratio is not None:
        score += 2 if debt_ratio < 0.5 else 1 if debt_ratio < 0.7 else 0
    if current_ratio is not None:
        score += 2 if current_ratio > 2 else 1 if current_ratio > 1.5 else 0
    return Component(
        name="balance_sheet",
        score=score,
        max_score=4,
        metrics={"debt_ratio": debt_ratio, "current_ratio": current_ratio},
    )


def analyze_cash_flow(snapshot: CompanySnapshot) -> Component:
    fcf = line_values(snapshot.line_items, "free_cash_flow")
    dividends = line_values(snapshot.line_items, "dividends_and_other_cash_distributions")
    score = 2.0 if fcf and fcf[0] > 0 else 0.0
    score += 1 if dividends and dividends[0] < 0 else 0
    return Component(
        name="cash_flow",
        score=score,
        max_score=3,
        metrics={
            "free_cash_flow": fcf[0] if fcf else None,
            "dividends": dividends[0] if dividends else None,
        },
    )


def analyze_management_actions(snapshot: CompanySnapshot) -> Component:
    issuance = line_values(snapshot.line_items, "issuance_or_purchase_of_equity_shares")
    value = issuance[0] if issuance else None
    score = 2.0 if value is not None and value < 0 else 1.0 if value == 0 else 0.0
    return Component(
        name="management_actions", score=score, max_score=2, metrics={"equity_issuance": value}
    )


def assess_quality_metrics(snapshot: CompanySnapshot) -> float:
    income = line_values(snapshot.line_items, "net_income")
    assets = line_values(snapshot.line_items, "total_assets")
    liabilities = line_values(snapshot.line_items, "total_liabilities")
    factors: list[float] = []
    if income and assets and liabilities and assets[0] and liabilities[0]:
        equity = assets[0] - liabilities[0]
        roe = income[0] / equity if equity > 0 and income[0] else 0
        factors.append(
            1.0
            if roe > 0.20
            else 0.8
            if roe > 0.15
            else 0.6
            if roe > 0.10
            else 0.3
            if equity > 0
            else 0.0
        )
    else:
        factors.append(0.5)
    if assets and liabilities and assets[0] and liabilities[0]:
        debt = liabilities[0] / assets[0]
        factors.append(1.0 if debt < 0.3 else 0.7 if debt < 0.5 else 0.4 if debt < 0.7 else 0.1)
    else:
        factors.append(0.5)
    positives = [value for value in income[:4] if value > 0]
    if len(positives) >= 3:
        declining = sum(
            positives[index - 1] > positives[index] for index in range(1, len(positives))
        )
        factors.append(1 - declining / (len(positives) - 1))
    else:
        factors.append(0.5)
    return sum(factors) / len(factors)


def calculate_intrinsic_value(snapshot: CompanySnapshot) -> float | None:
    incomes = [value for value in line_values(snapshot.line_items, "net_income")[:5] if value > 0]
    if not incomes:
        return None
    if len(incomes) < 2:
        return incomes[0] * 12
    historical = (
        (incomes[0] / incomes[-1]) ** (1 / (len(incomes) - 1)) - 1 if incomes[-1] > 0 else 0.05
    )
    sustainable = (
        0.20
        if historical > 0.25
        else historical * 0.8
        if historical > 0.15
        else historical * 0.9
        if historical > 0.05
        else 0.05
    )
    quality = assess_quality_metrics(snapshot)
    discount, multiple = (
        (0.12, 18) if quality >= 0.8 else (0.15, 15) if quality >= 0.6 else (0.18, 12)
    )
    earnings = incomes[0]
    dcf = sum(earnings * (1 + sustainable) ** year / (1 + discount) ** year for year in range(1, 6))
    terminal = earnings * (1 + sustainable) ** 5 * multiple / (1 + discount) ** 5
    return dcf + terminal


def score_jhunjhunwala(snapshot: CompanySnapshot) -> QuantScore:
    if not snapshot.line_items:
        return no_data(Domain.FUNDAMENTALS, snapshot.ticker, "financial line items unavailable")
    components = [
        analyze_growth(snapshot),
        analyze_profitability(snapshot),
        analyze_balance_sheet(snapshot),
        analyze_cash_flow(snapshot),
        analyze_management_actions(snapshot),
    ]
    total = sum(component.score or 0 for component in components)
    intrinsic = calculate_intrinsic_value(snapshot)
    margin = (
        (intrinsic - snapshot.market_cap) / snapshot.market_cap
        if intrinsic and snapshot.market_cap
        else None
    )
    quality = assess_quality_metrics(snapshot)
    if margin is not None and margin >= 0.30:
        signal = Signal.BULLISH
    elif margin is not None and margin <= -0.30:
        signal = Signal.BEARISH
    elif quality >= 0.7 and total >= MAX_SCORE * 0.6:
        signal = Signal.BULLISH
    elif quality <= 0.4 or total <= MAX_SCORE * 0.3:
        signal = Signal.BEARISH
    else:
        signal = Signal.NEUTRAL
    confidence = (
        min(max(abs(margin) * 150, 20), 95)
        if margin is not None
        else min(max(total / MAX_SCORE * 100, 10), 80)
    )
    components.append(
        Component(
            name="intrinsic_value",
            metrics={
                "intrinsic_value": intrinsic,
                "market_cap": snapshot.market_cap,
                "margin_of_safety": margin,
                "quality_score": quality,
            },
        )
    )
    return QuantScore(
        analyst=Domain.FUNDAMENTALS,
        ticker=snapshot.ticker,
        signal=signal,
        confidence=confidence,
        score=total,
        max_score=MAX_SCORE,
        components=components,
    )
