"""Benjamin Graham: earnings stability, balance-sheet strength, net-net and the Graham Number.

Ported from ai-hedge-fund ``src/agents/ben_graham.py`` (MIT). The score (out of 15) and its
70%/30% signal bands are deterministic there too. The LLM only wrote the prose and could
override the signal; here the score decides and the model may only explain it.
Line items are annual, newest first.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from fin_graph.domain.market import FinancialMetrics, LineItem
from fin_graph.domain.quant.common import Component, QuantScore, no_data, score_signal
from fin_graph.domain.tools import Domain

MAX_SCORE = 15
#: Graham's multiplier: a P/E of 15 times a P/B of 1.5.
GRAHAM_MULTIPLIER = 22.5


def earnings_stability(metrics: Sequence[FinancialMetrics], items: Sequence[LineItem]) -> Component:
    """Positive EPS every period (3) or in 80% of them (2), plus 1 if EPS grew."""
    if not metrics or not items:
        return Component(
            name="earnings_stability", score=0, max_score=4, details="Insufficient data"
        )
    eps = [value for item in items if (value := item.value("earnings_per_share")) is not None]
    if len(eps) < 2:
        return Component(
            name="earnings_stability",
            score=0,
            max_score=4,
            details="Not enough multi-year EPS data",
        )
    score, notes = 0, []
    positive = sum(value > 0 for value in eps)
    if positive == len(eps):
        score += 3
        notes.append("EPS was positive in all available periods")
    elif positive >= len(eps) * 0.8:
        score += 2
        notes.append("EPS was positive in most periods")
    else:
        notes.append("EPS was negative in multiple periods")
    if eps[0] > eps[-1]:
        score += 1
        notes.append("EPS grew from earliest to latest period")
    else:
        notes.append("EPS did not grow from earliest to latest period")
    return Component(
        name="earnings_stability",
        score=score,
        max_score=4,
        details="; ".join(notes),
        metrics={"positive_eps_periods": positive, "eps_periods": len(eps), "eps_latest": eps[0]},
    )


def financial_strength(items: Sequence[LineItem]) -> Component:
    """Liquidity, leverage and dividends.

    Current ratio ≥ 2 (2) or ≥ 1.5 (1); liabilities/assets < 0.5 (2) or < 0.8 (1); dividends
    paid in most years (1).
    """
    if not items:
        return Component(name="financial_strength", score=0, max_score=5, details="No data")
    latest = items[0]
    assets = latest.value("total_assets") or 0
    liabilities = latest.value("total_liabilities") or 0
    current_assets = latest.value("current_assets") or 0
    current_liabilities = latest.value("current_liabilities") or 0
    score, notes = 0, []
    metrics: dict[str, float | None] = {"current_ratio": None, "debt_ratio": None}
    if current_liabilities > 0:
        current = current_assets / current_liabilities
        metrics["current_ratio"] = current
        if current >= 2.0:
            score += 2
            notes.append(f"Current ratio = {current:.2f} (>=2.0: solid)")
        elif current >= 1.5:
            score += 1
            notes.append(f"Current ratio = {current:.2f} (moderately strong)")
        else:
            notes.append(f"Current ratio = {current:.2f} (<1.5: weaker liquidity)")
    else:
        notes.append("Cannot compute current ratio")
    if assets > 0:
        debt_ratio = liabilities / assets
        metrics["debt_ratio"] = debt_ratio
        if debt_ratio < 0.5:
            score += 2
            notes.append(f"Debt ratio = {debt_ratio:.2f}, under 0.50 (conservative)")
        elif debt_ratio < 0.8:
            score += 1
            notes.append(f"Debt ratio = {debt_ratio:.2f}, somewhat high but acceptable")
        else:
            notes.append(f"Debt ratio = {debt_ratio:.2f}, quite high by Graham standards")
    else:
        notes.append("Cannot compute debt ratio")
    dividends = [
        value
        for item in items
        if (value := item.value("dividends_and_other_cash_distributions")) is not None
    ]
    paid = sum(value < 0 for value in dividends)
    if dividends and paid >= len(dividends) // 2 + 1:
        score += 1
        notes.append("Paid dividends in the majority of the reported years")
    elif paid:
        notes.append("Some dividend payments, but not most years")
    elif dividends:
        notes.append("Did not pay dividends in these periods")
    else:
        notes.append("No dividend data available")
    metrics["dividend_years"] = paid
    return Component(
        name="financial_strength",
        score=score,
        max_score=5,
        details="; ".join(notes),
        metrics=metrics,
    )


def graham_valuation(items: Sequence[LineItem], market_cap: float | None) -> Component:
    """Net-net (4, or 2 for NCAV ≥ 2/3 of price) and Graham Number margin > 50% (3), > 20% (1)."""
    if not items or not market_cap or market_cap <= 0:
        return Component(name="valuation", score=0, max_score=7, details="Insufficient data")
    latest = items[0]
    current_assets = latest.value("current_assets") or 0
    liabilities = latest.value("total_liabilities") or 0
    book_per_share = latest.value("book_value_per_share") or 0
    eps = latest.value("earnings_per_share") or 0
    shares = latest.value("outstanding_shares") or 0
    score, notes = 0, []
    ncav = current_assets - liabilities
    price = market_cap / shares if shares > 0 else None
    if ncav > 0 and shares > 0 and price is not None:
        ncav_per_share = ncav / shares
        notes.append(f"NCAV per share = {ncav_per_share:,.2f} vs price {price:,.2f}")
        if ncav > market_cap:
            score += 4
            notes.append("Net-net: NCAV > market cap (classic Graham deep value)")
        elif ncav_per_share >= price * 0.67:
            score += 2
            notes.append("NCAV per share >= 2/3 of price (moderate net-net discount)")
    else:
        notes.append("NCAV not positive or shares unknown")
    graham_number = (
        math.sqrt(GRAHAM_MULTIPLIER * eps * book_per_share)
        if eps > 0 and book_per_share > 0
        else None
    )
    margin = None
    if graham_number is None:
        notes.append("Unable to compute Graham Number (EPS or book value missing or <= 0)")
    elif price is not None and price > 0:
        margin = (graham_number - price) / price
        notes.append(f"Graham Number = {graham_number:.2f}, margin of safety {margin:.1%}")
        if margin > 0.5:
            score += 3
        elif margin > 0.2:
            score += 1
    return Component(
        name="valuation",
        score=score,
        max_score=7,
        details="; ".join(notes),
        metrics={
            "ncav": ncav,
            "price_per_share": price,
            "graham_number": graham_number,
            "margin_of_safety": margin,
        },
    )


def score_graham(
    ticker: str,
    metrics: Sequence[FinancialMetrics],
    items: Sequence[LineItem],
    market_cap: float | None,
) -> QuantScore:
    """Sum the three parts out of 15: ≥ 70% bullish, ≤ 30% bearish, otherwise neutral.

    Confidence is the distance from the midpoint (7.5 → 0, 0 or 15 → 100); the original left
    it to the LLM.
    """
    if not items:
        return no_data(Domain.GRAHAM, ticker, "no annual line items")
    components = [
        earnings_stability(metrics, items),
        financial_strength(items),
        graham_valuation(items, market_cap),
    ]
    total = sum(item.score or 0 for item in components)
    signal = score_signal(total, MAX_SCORE)
    gaps = [] if market_cap else ["market cap unavailable"]
    return QuantScore(
        analyst=Domain.GRAHAM,
        ticker=ticker,
        signal=signal,
        # The parts add up to 16 against a declared maximum of 15, as in the original.
        confidence=min(100, round(abs(total / MAX_SCORE - 0.5) * 200)),
        score=total,
        max_score=MAX_SCORE,
        components=components,
        data_gaps=gaps,
    )
