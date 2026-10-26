"""Valuation: four intrinsic-value methods weighted against the market cap.

Ported from ai-hedge-fund ``src/agents/valuation.py`` (MIT): scenario DCF on a WACC (35%),
owner earnings (35%), EV/EBITDA (20%) and residual income (10%). One correction: capital
expenditure is subtracted as an outflow whatever its sign. Providers report it negative, and
the original added it back, which inflated owner earnings. EV/EBITDA needs two or more periods:
with only today's multiple the original returned the market cap itself, a 0% gap that diluted
the weighted gap.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from fin_graph.domain.market import FinancialMetrics, LineItem
from fin_graph.domain.quant.common import Component, QuantScore, Signal, no_data, num
from fin_graph.domain.tools import Domain

#: A gap beyond this fraction of market cap leans bullish (above) or bearish (below).
SIGNAL_GAP = 0.15
#: The weighted gap that earns 100% confidence.
FULL_CONFIDENCE_GAP = 0.30
WEIGHTS = {"dcf": 0.35, "owner_earnings": 0.35, "ev_ebitda": 0.20, "residual_income": 0.10}
_SCENARIOS = {
    "bear": (0.5, 1.2),
    "base": (1.0, 1.0),
    "bull": (1.5, 0.9),
}
_SCENARIO_WEIGHTS = {"bear": 0.2, "base": 0.6, "bull": 0.2}


def owner_earnings_value(
    net_income: float | None,
    depreciation: float | None,
    capex: float | None,
    working_capital_change: float | None,
    growth_rate: float = 0.05,
    required_return: float = 0.15,
    margin_of_safety: float = 0.25,
    num_years: int = 5,
) -> float:
    """Buffett owner-earnings DCF with a margin of safety; 0 when inputs are missing or negative."""
    if net_income is None or depreciation is None or capex is None:
        return 0.0
    if working_capital_change is None:
        return 0.0
    owner_earnings = net_income + depreciation - abs(capex) - working_capital_change
    if owner_earnings <= 0:
        return 0.0
    present = sum(
        owner_earnings * (1 + growth_rate) ** year / (1 + required_return) ** year
        for year in range(1, num_years + 1)
    )
    terminal_growth = min(growth_rate, 0.03)
    terminal = (owner_earnings * (1 + growth_rate) ** num_years * (1 + terminal_growth)) / (
        required_return - terminal_growth
    )
    present_terminal = terminal / (1 + required_return) ** num_years
    return (present + present_terminal) * (1 - margin_of_safety)


def wacc(
    market_cap: float,
    total_debt: float | None,
    cash: float | None,
    interest_coverage: float | None,
    *,
    beta: float = 1.0,
    risk_free_rate: float = 0.045,
    market_risk_premium: float = 0.06,
) -> float:
    """CAPM cost of equity, debt cost from interest coverage, 25% tax shield; kept in 6%-20%."""
    cost_of_equity = risk_free_rate + beta * market_risk_premium
    if interest_coverage and interest_coverage > 0:
        cost_of_debt = max(risk_free_rate + 0.01, risk_free_rate + 10 / interest_coverage)
    else:
        cost_of_debt = risk_free_rate + 0.05
    net_debt = max((total_debt or 0) - (cash or 0), 0)
    total = market_cap + net_debt
    if total > 0:
        rate = market_cap / total * cost_of_equity + net_debt / total * cost_of_debt * 0.75
    else:
        rate = cost_of_equity
    return min(max(rate, 0.06), 0.20)


def fcf_volatility(fcf_history: Sequence[float]) -> float:
    """Coefficient of variation of the positive FCF periods, capped at 1."""
    if len(fcf_history) < 3:
        return 0.5
    positive = [value for value in fcf_history if value > 0]
    if len(positive) < 2:
        return 0.8
    mean = statistics.mean(positive)
    return min(statistics.stdev(positive) / mean, 1.0) if mean > 0 else 0.8


def multistage_dcf(
    fcf_history: Sequence[float], discount_rate: float, market_cap: float, revenue_growth: float
) -> float:
    """Three-stage DCF (3y high growth, 4y fading, terminal) with an FCF-quality haircut."""
    if not fcf_history or fcf_history[0] <= 0:
        return 0.0
    current = fcf_history[0]
    average = sum(fcf_history[:3]) / min(3, len(fcf_history))
    high = min(revenue_growth, 0.25) if revenue_growth else 0.05
    if market_cap > 50_000_000_000:
        high = min(high, 0.10)
    transition = (high + 0.03) / 2
    terminal_growth = min(0.03, high * 0.6)
    base = max(current, average * 0.85)
    present = sum(base * (1 + high) ** year / (1 + discount_rate) ** year for year in range(1, 4))
    for year in range(4, 8):
        fading = transition * (8 - year) / 4
        present += base * (1 + high) ** 3 * (1 + fading) ** (year - 3) / (1 + discount_rate) ** year
    final = base * (1 + high) ** 3 * (1 + transition) ** 4
    if discount_rate <= terminal_growth:
        terminal_growth = discount_rate * 0.8
    terminal = final * (1 + terminal_growth) / (discount_rate - terminal_growth)
    present += terminal / (1 + discount_rate) ** 7
    quality = max(0.7, 1 - fcf_volatility(fcf_history) * 0.5)
    return present * quality


@dataclass(frozen=True)
class DcfScenarios:
    """Bear/base/bull DCF values and their 20/60/20 probability-weighted mean."""

    bear: float
    base: float
    bull: float

    @property
    def expected(self) -> float:
        return (
            self.bear * _SCENARIO_WEIGHTS["bear"]
            + self.base * _SCENARIO_WEIGHTS["base"]
            + self.bull * _SCENARIO_WEIGHTS["bull"]
        )


def dcf_scenarios(
    fcf_history: Sequence[float],
    discount_rate: float,
    market_cap: float,
    revenue_growth: float | None,
) -> DcfScenarios:
    """The multistage DCF with growth and discount rate shocked per scenario."""
    growth = revenue_growth or 0.05
    values = {
        name: multistage_dcf(fcf_history, discount_rate * rate_adj, market_cap, growth * growth_adj)
        for name, (growth_adj, rate_adj) in _SCENARIOS.items()
    }
    return DcfScenarios(bear=values["bear"], base=values["base"], bull=values["bull"])


def ev_ebitda_value(metrics: Sequence[FinancialMetrics]) -> float:
    """Equity implied by the median historical EV/EBITDA on today's EBITDA, minus net debt.

    Needs at least two periods with a multiple; Yahoo gives only the current one.
    """
    if not metrics:
        return 0.0
    latest = metrics[0]
    if not latest.enterprise_value or not latest.enterprise_value_to_ebitda_ratio:
        return 0.0
    ebitda = latest.enterprise_value / latest.enterprise_value_to_ebitda_ratio
    multiples = [
        item.enterprise_value_to_ebitda_ratio
        for item in metrics
        if item.enterprise_value_to_ebitda_ratio
    ]
    if len(multiples) < 2:
        # One multiple is today's: its "median" implies today's price, a gap of exactly 0.
        return 0.0
    implied = statistics.median(multiples) * ebitda
    net_debt = latest.enterprise_value - (latest.market_cap or 0)
    return max(implied - net_debt, 0.0)


def residual_income_value(
    market_cap: float | None,
    net_income: float | None,
    price_to_book: float | None,
    book_value_growth: float = 0.03,
    cost_of_equity: float = 0.10,
    terminal_growth: float = 0.03,
    num_years: int = 5,
) -> float:
    """Edwards-Bell-Ohlson residual income on book value, with a 20% margin of safety."""
    if not (market_cap and net_income and price_to_book and price_to_book > 0):
        return 0.0
    book = market_cap / price_to_book
    residual = net_income - cost_of_equity * book
    if residual <= 0:
        return 0.0
    present = sum(
        residual * (1 + book_value_growth) ** year / (1 + cost_of_equity) ** year
        for year in range(1, num_years + 1)
    )
    terminal = (
        residual * (1 + book_value_growth) ** (num_years + 1) / (cost_of_equity - terminal_growth)
    )
    present += terminal / (1 + cost_of_equity) ** num_years
    return (book + present) * 0.8


def _gap_signal(gap: float | None) -> Signal:
    if gap is not None and gap > SIGNAL_GAP:
        return Signal.BULLISH
    if gap is not None and gap < -SIGNAL_GAP:
        return Signal.BEARISH
    return Signal.NEUTRAL


def score_valuation(
    ticker: str,
    metrics: Sequence[FinancialMetrics],
    line_items: Sequence[LineItem],
    market_cap: float | None,
    *,
    risk_free_rate: float = 0.045,
) -> QuantScore:
    """Weighted gap between intrinsic value and ``market_cap``; newest period first."""
    if not metrics:
        return no_data(Domain.VALUATION, ticker, "no financial metrics")
    if len(line_items) < 2:
        return no_data(Domain.VALUATION, ticker, "fewer than two periods of line items")
    if not market_cap or market_cap <= 0:
        return no_data(Domain.VALUATION, ticker, "market cap unavailable")
    latest_metrics, current, previous = metrics[0], line_items[0], line_items[1]
    gaps: list[str] = []

    wc_now, wc_before = current.value("working_capital"), previous.value("working_capital")
    if wc_now is None or wc_before is None:
        gaps.append("working capital change unknown; assumed 0")
    wc_change = (wc_now - wc_before) if wc_now is not None and wc_before is not None else 0.0
    owner = owner_earnings_value(
        current.value("net_income"),
        current.value("depreciation_and_amortization"),
        current.value("capital_expenditure"),
        wc_change,
        growth_rate=latest_metrics.earnings_growth or 0.05,
    )
    rate = wacc(
        latest_metrics.market_cap or 0,
        current.value("total_debt"),
        current.value("cash_and_equivalents"),
        latest_metrics.interest_coverage,
        risk_free_rate=risk_free_rate,
    )
    fcf_history = [
        value for item in line_items if (value := item.value("free_cash_flow")) is not None
    ]
    scenarios = dcf_scenarios(
        fcf_history, rate, latest_metrics.market_cap or 0, latest_metrics.revenue_growth
    )
    values = {
        "dcf": scenarios.expected,
        "owner_earnings": owner,
        "ev_ebitda": ev_ebitda_value(metrics),
        "residual_income": residual_income_value(
            latest_metrics.market_cap,
            current.value("net_income"),
            latest_metrics.price_to_book_ratio,
            latest_metrics.book_value_growth or 0.03,
        ),
    }
    total_weight = sum(WEIGHTS[name] for name, value in values.items() if value > 0)
    if total_weight == 0:
        return no_data(Domain.VALUATION, ticker, "every valuation method returned zero")
    method_gaps = {
        name: (value - market_cap) / market_cap if value > 0 else None
        for name, value in values.items()
    }
    weighted_gap = (
        sum(WEIGHTS[name] * gap for name, gap in method_gaps.items() if gap is not None)
        / total_weight
    )
    components = []
    for name, value in values.items():
        gap = method_gaps[name]
        if gap is None:
            gaps.append(f"{name} could not be computed")
            continue
        details = (
            f"Value: ${num(value)}, Market Cap: ${num(market_cap)}, Gap: {gap:.1%}, "
            f"Weight: {WEIGHTS[name]:.0%}"
        )
        if name == "dcf":
            details += (
                f"; WACC: {rate:.1%}, Bear: ${num(scenarios.bear)}, Base: ${num(scenarios.base)}, "
                f"Bull: ${num(scenarios.bull)}"
            )
        components.append(
            Component(
                name=name,
                signal=_gap_signal(gap),
                details=details,
                metrics={"value": value, "gap": gap, "weight": WEIGHTS[name]},
            )
        )
    return QuantScore(
        analyst=Domain.VALUATION,
        ticker=ticker,
        signal=_gap_signal(weighted_gap),
        confidence=round(min(abs(weighted_gap) / FULL_CONFIDENCE_GAP * 100, 100)),
        score=round(weighted_gap, 4),
        components=[
            *components,
            Component(
                name="dcf_scenarios",
                details=f"WACC {rate:.1%} over {len(fcf_history)} FCF periods",
                metrics={
                    "wacc": rate,
                    "bear": scenarios.bear,
                    "base": scenarios.base,
                    "bull": scenarios.bull,
                    "fcf_periods": len(fcf_history),
                    "weighted_gap": weighted_gap,
                },
            ),
        ],
        data_gaps=gaps,
    )
