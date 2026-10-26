"""Warren Buffett: quality (fundamentals, consistency, moat, pricing power, book value, management)
and an owner-earnings intrinsic value against the market cap.

Ported from ai-hedge-fund ``src/agents/warren_buffett.py`` (MIT); every sub-score is unchanged,
except that pricing power falls back to gross profit over revenue when no gross margin is given.
There the final signal came from the LLM. Here it is deterministic: quality at or above 70%
of the maximum with a positive margin of safety is bullish, quality at or below 30% or a
margin below -30% is bearish, and anything else is neutral. Line items are newest first.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from fin_graph.domain.market import FinancialMetrics, LineItem
from fin_graph.domain.quant.common import Component, QuantScore, Signal, no_data
from fin_graph.domain.tools import Domain

#: Fundamentals (10) + moat (5) + management (2) + pricing power (5) + book value (5).
MAX_SCORE = 27
#: A margin of safety below this is bearish whatever the quality.
EXPENSIVE_MARGIN = -0.3


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def fundamentals(metrics: Sequence[FinancialMetrics]) -> Component:
    """ROE > 15% (2), D/E < 0.5 (2), operating margin > 15% (2), current ratio > 1.5 (1)."""
    if not metrics:
        return Component(name="fundamentals", score=0, max_score=10, details="Insufficient data")
    latest = metrics[0]
    score, notes = 0, []
    if latest.return_on_equity and latest.return_on_equity > 0.15:
        score += 2
        notes.append(f"Strong ROE of {latest.return_on_equity:.1%}")
    elif latest.return_on_equity:
        notes.append(f"Weak ROE of {latest.return_on_equity:.1%}")
    if latest.debt_to_equity and latest.debt_to_equity < 0.5:
        score += 2
        notes.append("Conservative debt levels")
    elif latest.debt_to_equity:
        notes.append(f"High debt to equity ratio of {latest.debt_to_equity:.1f}")
    if latest.operating_margin and latest.operating_margin > 0.15:
        score += 2
        notes.append("Strong operating margins")
    elif latest.operating_margin:
        notes.append(f"Weak operating margin of {latest.operating_margin:.1%}")
    if latest.current_ratio and latest.current_ratio > 1.5:
        score += 1
        notes.append("Good liquidity position")
    elif latest.current_ratio:
        notes.append(f"Weak liquidity with current ratio of {latest.current_ratio:.1f}")
    return Component(name="fundamentals", score=score, max_score=10, details="; ".join(notes))


def consistency(items: Sequence[LineItem]) -> Component:
    """Net income rising every period across at least four periods (3)."""
    if len(items) < 4:
        return Component(name="consistency", score=0, details="Insufficient historical data")
    earnings = [value for item in items if (value := item.value("net_income"))]
    if len(earnings) < 4:
        return Component(name="consistency", score=0, details="Insufficient earnings data")
    rising = all(earnings[index] > earnings[index + 1] for index in range(len(earnings) - 1))
    growth = (earnings[0] - earnings[-1]) / abs(earnings[-1]) if earnings[-1] else None
    note = "Consistent earnings growth" if rising else "Inconsistent earnings growth pattern"
    if growth is not None:
        note += f"; total growth {growth:.1%} over {len(earnings)} periods"
    return Component(
        name="consistency", score=3 if rising else 0, details=note, metrics={"growth": growth}
    )


def moat(metrics: Sequence[FinancialMetrics]) -> Component:
    """Durable advantage from ROE consistency, margin level and trend, asset turns, stability."""
    if len(metrics) < 5:
        return Component(name="moat", score=0, max_score=5, details="Insufficient data")
    score, notes = 0, []
    roes = [item.return_on_equity for item in metrics if item.return_on_equity is not None]
    margins = [item.operating_margin for item in metrics if item.operating_margin is not None]
    if len(roes) >= 5:
        high = sum(value > 0.15 for value in roes)
        share = high / len(roes)
        if share >= 0.8:
            score += 2
            notes.append(f"Excellent ROE consistency: {high}/{len(roes)} periods >15%")
        elif share >= 0.6:
            score += 1
            notes.append(f"Good ROE performance: {high}/{len(roes)} periods >15%")
        else:
            notes.append(f"Inconsistent ROE: only {high}/{len(roes)} periods >15%")
    if len(margins) >= 5:
        average = _mean(margins)
        if average > 0.2 and _mean(margins[:3]) >= _mean(margins[-3:]):
            score += 1
            notes.append(f"Strong and stable operating margins (avg {average:.1%})")
        else:
            notes.append(f"Operating margins average {average:.1%}")
    turns = [item.asset_turnover for item in metrics if item.asset_turnover is not None]
    if len(turns) >= 3 and any(value > 1.0 for value in turns):
        score += 1
        notes.append("Efficient asset utilization")
    if len(roes) >= 5 and len(margins) >= 5:
        stability = (_stability(roes) + _stability(margins)) / 2
        if stability > 0.7:
            score += 1
            notes.append(f"High performance stability ({stability:.1%})")
    return Component(name="moat", score=min(score, 5), max_score=5, details="; ".join(notes))


def _stability(values: Sequence[float]) -> float:
    """1 minus the coefficient of variation (population), 0 for a non-positive mean."""
    average = _mean(values)
    if average <= 0:
        return 0.0
    variance = sum((value - average) ** 2 for value in values) / len(values)
    return 1 - math.sqrt(variance) / average


def management(items: Sequence[LineItem]) -> Component:
    """Buybacks (1) and dividends (1) in the latest period; issuance is noted as dilution."""
    if not items:
        return Component(name="management", score=0, max_score=2, details="Insufficient data")
    latest = items[0]
    score, notes = 0, []
    equity_flow = latest.value("issuance_or_purchase_of_equity_shares")
    if equity_flow and equity_flow < 0:
        score += 1
        notes.append("Repurchasing shares (shareholder-friendly)")
    if equity_flow and equity_flow > 0:
        notes.append("Recent stock issuance (potential dilution)")
    else:
        notes.append("No significant new stock issuance")
    dividends = latest.value("dividends_and_other_cash_distributions")
    if dividends and dividends < 0:
        score += 1
        notes.append("Pays dividends")
    else:
        notes.append("No or minimal dividends")
    return Component(name="management", score=score, max_score=2, details="; ".join(notes))


def pricing_power(items: Sequence[LineItem], metrics: Sequence[FinancialMetrics]) -> Component:
    """Gross-margin trend (up to 3) and level above 50% (2) or 30% (1)."""
    if not items or not metrics:
        return Component(name="pricing_power", score=0, max_score=5, details="Insufficient data")
    margins = [value for item in items if (value := _gross_margin(item)) is not None]
    score, notes = 0, []
    if len(margins) >= 3:
        recent, older = _mean(margins[:2]), _mean(margins[-2:])
        if recent > older + 0.02:
            score += 3
            notes.append("Expanding gross margins")
        elif recent > older:
            score += 2
            notes.append("Improving gross margins")
        elif abs(recent - older) < 0.01:
            score += 1
            notes.append("Stable gross margins")
        else:
            notes.append("Declining gross margins")
    if margins:
        average = _mean(margins)
        if average > 0.5:
            score += 2
            notes.append(f"High gross margins ({average:.1%})")
        elif average > 0.3:
            score += 1
            notes.append(f"Good gross margins ({average:.1%})")
    return Component(name="pricing_power", score=score, max_score=5, details="; ".join(notes))


def _gross_margin(item: LineItem) -> float | None:
    """The reported gross margin, else gross profit over revenue."""
    reported = item.value("gross_margin")
    if reported is not None:
        return reported
    profit, revenue = item.value("gross_profit"), item.value("revenue")
    return profit / revenue if profit is not None and revenue else None


def book_value_growth(items: Sequence[LineItem]) -> Component:
    """Book value per share rising in most periods (up to 3) and its CAGR (up to 3)."""
    if len(items) < 3:
        return Component(name="book_value", score=0, max_score=5, details="Insufficient data")
    books = []
    for item in items:
        equity, shares = item.value("shareholders_equity"), item.value("outstanding_shares")
        if equity and shares:
            books.append(equity / shares)
    if len(books) < 3:
        return Component(name="book_value", score=0, max_score=5, details="Insufficient data")
    rising = sum(books[index] > books[index + 1] for index in range(len(books) - 1))
    share = rising / (len(books) - 1)
    score = 3 if share >= 0.8 else 2 if share >= 0.6 else 1 if share >= 0.4 else 0
    cagr_points, note = _book_cagr(books)
    return Component(
        name="book_value",
        score=score + cagr_points,
        max_score=5,
        details=f"Book value per share rose in {rising}/{len(books) - 1} periods; {note}",
    )


def _book_cagr(books: Sequence[float]) -> tuple[int, str]:
    oldest, latest = books[-1], books[0]
    years = len(books) - 1
    if oldest > 0 and latest > 0:
        cagr = (latest / oldest) ** (1 / years) - 1
        points = 2 if cagr > 0.15 else 1 if cagr > 0.1 else 0
        return points, f"book value CAGR {cagr:.1%}"
    if oldest < 0 < latest:
        return 3, "book value turned from negative to positive"
    return 0, "book value CAGR not meaningful"


def maintenance_capex(items: Sequence[LineItem]) -> float:
    """Median of 85% of capex, depreciation and the average capex/revenue on current revenue."""
    if not items:
        return 0.0
    ratios = []
    for item in items[:5]:
        capex, revenue = item.value("capital_expenditure"), item.value("revenue")
        if capex and revenue and revenue > 0:
            ratios.append(abs(capex) / revenue)
    depreciation = items[0].value("depreciation_and_amortization") or 0
    capex = abs(items[0].value("capital_expenditure") or 0)
    estimates = [capex * 0.85, depreciation]
    if len(ratios) >= 3:
        revenue = items[0].value("revenue") or 0
        estimates.append(_mean(ratios) * revenue if revenue else 0)
        return sorted(estimates)[1]
    return max(estimates)


@dataclass(frozen=True)
class IntrinsicValue:
    """The conservative owner-earnings DCF and the numbers it rests on."""

    value: float | None
    owner_earnings: float | None = None
    growth: float | None = None
    details: str = ""


def intrinsic_value(items: Sequence[LineItem]) -> IntrinsicValue:
    """Three-stage owner-earnings DCF at 10% with a 15% haircut; ``None`` without the inputs."""
    if len(items) < 3:
        return IntrinsicValue(None, details="Insufficient data for reliable valuation")
    latest, previous = items[0], items[1]
    net_income = latest.value("net_income")
    depreciation = latest.value("depreciation_and_amortization")
    capex = latest.value("capital_expenditure")
    if net_income is None or depreciation is None or capex is None:
        return IntrinsicValue(None, details="Missing net income, depreciation or capex")
    working_capital_change = 0.0
    parts = [
        latest.value("current_assets"),
        latest.value("current_liabilities"),
        previous.value("current_assets"),
        previous.value("current_liabilities"),
    ]
    if all(parts):
        ca, cl, ca_prev, cl_prev = (float(part or 0) for part in parts)
        working_capital_change = (ca - cl) - (ca_prev - cl_prev)
    owner = net_income + depreciation - maintenance_capex(items) - working_capital_change
    if not owner:
        return IntrinsicValue(None, details="Owner earnings are zero")
    shares = latest.value("outstanding_shares")
    if not shares or shares <= 0:
        return IntrinsicValue(None, owner, details="Missing or invalid shares outstanding")
    history = [value for item in items[:5] if (value := item.value("net_income"))]
    growth = 0.03
    if len(history) >= 3 and history[-1] > 0:
        historical = (history[0] / history[-1]) ** (1 / (len(history) - 1)) - 1
        growth = max(-0.05, min(historical, 0.15)) * 0.7
    stage1, stage2, terminal_growth, rate = min(growth, 0.08), min(growth * 0.5, 0.04), 0.025, 0.10
    present = sum(owner * (1 + stage1) ** year / (1 + rate) ** year for year in range(1, 6))
    stage1_final = owner * (1 + stage1) ** 5
    present += sum(
        stage1_final * (1 + stage2) ** year / (1 + rate) ** (5 + year) for year in range(1, 6)
    )
    final = stage1_final * (1 + stage2) ** 5
    present += final * (1 + terminal_growth) / (rate - terminal_growth) / (1 + rate) ** 10
    return IntrinsicValue(
        value=present * 0.85,
        owner_earnings=owner,
        growth=growth,
        details=f"Owner earnings {owner:,.0f}; stage growth {stage1:.1%}/{stage2:.1%}; "
        f"discount {rate:.0%}; 15% haircut",
    )


def score_buffett(
    ticker: str,
    metrics: Sequence[FinancialMetrics],
    items: Sequence[LineItem],
    market_cap: float | None,
) -> QuantScore:
    """Quality score out of 27 plus the margin of safety of the owner-earnings value."""
    if not metrics and not items:
        return no_data(Domain.BUFFETT, ticker, "no financial metrics and no line items")
    components = [
        fundamentals(metrics),
        consistency(items),
        moat(metrics),
        pricing_power(items, metrics),
        book_value_growth(items),
        management(items),
    ]
    total = sum(item.score or 0 for item in components)
    value = intrinsic_value(items)
    margin = (
        (value.value - market_cap) / market_cap if value.value is not None and market_cap else None
    )
    quality = total / MAX_SCORE
    if quality >= 0.7 and margin is not None and margin > 0:
        signal = Signal.BULLISH
    elif quality <= 0.3 or (margin is not None and margin < EXPENSIVE_MARGIN):
        signal = Signal.BEARISH
    else:
        signal = Signal.NEUTRAL
    # Consistency adds up to 3 points outside MAX_SCORE, as in the original, so cap at 100.
    confidence = min(100, round(abs(quality - 0.5) * 200))
    gaps = (
        [] if margin is not None else ["no margin of safety: intrinsic value or market cap missing"]
    )
    return QuantScore(
        analyst=Domain.BUFFETT,
        ticker=ticker,
        signal=signal,
        confidence=confidence,
        score=total,
        max_score=MAX_SCORE,
        components=[
            *components,
            Component(
                name="intrinsic_value",
                details=value.details,
                metrics={
                    "intrinsic_value": value.value,
                    "owner_earnings": value.owner_earnings,
                    "market_cap": market_cap,
                    "margin_of_safety": margin,
                },
            ),
        ],
        data_gaps=gaps,
    )
