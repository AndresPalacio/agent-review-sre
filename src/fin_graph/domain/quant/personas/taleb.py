"""Nassim Taleb-style tail-risk, antifragility, convexity and fragility scoring."""

from __future__ import annotations

import math
import statistics
from itertools import pairwise

from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.quant.common import Component, QuantScore, no_data, score_signal
from fin_graph.domain.quant.personas import component_total, midpoint_confidence
from fin_graph.domain.quant.personas._growth_common import insider_component, line_values
from fin_graph.domain.tools import Domain

MAX_SCORE = 11.0


def _returns(snapshot: CompanySnapshot) -> list[float]:
    closes = [price.close for price in snapshot.prices]
    if any(not math.isfinite(value) for value in closes):
        raise ValueError("non-finite price in tail-risk input")
    if any(value <= 0 for value in closes):
        raise ValueError("non-positive price in tail-risk input")
    return [current / previous - 1 for previous, current in pairwise(closes)]


def analyze_tail_risk(snapshot: CompanySnapshot) -> Component:
    closes = [price.close for price in snapshot.prices]
    returns = _returns(snapshot)
    if len(returns) < 4:
        return Component(
            name="tail_risk",
            score=0,
            max_score=2,
            details="Insufficient price history",
            metrics={"max_drawdown": None, "worst_return": None},
        )
    peak = closes[0]
    max_drawdown = 0.0
    for close in closes:
        peak = max(peak, close)
        max_drawdown = min(max_drawdown, close / peak - 1)
    worst_return = min(returns)
    score = 2.0 if max_drawdown > -0.10 else 1.0 if max_drawdown > -0.25 else 0.0
    return Component(
        name="tail_risk",
        score=score,
        max_score=2,
        details=f"Max drawdown {max_drawdown:.1%}; worst return {worst_return:.1%}",
        metrics={"max_drawdown": max_drawdown, "worst_return": worst_return},
    )


def analyze_antifragility(snapshot: CompanySnapshot) -> Component:
    latest = snapshot.line_items[0] if snapshot.line_items else None
    cash = latest.value("cash_and_equivalents") if latest else None
    debt = latest.value("total_debt") if latest else None
    cash_flows = line_values(snapshot.line_items, "free_cash_flow")
    score = 0.0
    if cash is not None and debt is not None and cash > debt:
        score += 1
    if cash_flows and all(value > 0 for value in cash_flows):
        score += 1
    return Component(
        name="antifragility",
        score=score,
        max_score=2,
        details=f"Cash {cash}; debt {debt}; positive FCF periods {sum(v > 0 for v in cash_flows)}",
        metrics={"cash": cash, "debt": debt},
    )


def analyze_convexity(snapshot: CompanySnapshot) -> Component:
    returns = _returns(snapshot)
    gains = [value for value in returns if value > 0]
    losses = [abs(value) for value in returns if value < 0]
    average_gain = sum(gains) / len(gains) if gains else 0.0
    average_loss = sum(losses) / len(losses) if losses else 0.0
    ratio = average_gain / average_loss if average_loss else None
    score = (
        2.0
        if ratio is not None and ratio > 1.5
        else 1.0
        if ratio is not None and ratio > 1
        else 0.0
    )
    return Component(
        name="convexity",
        score=score,
        max_score=2,
        details=f"Average upside/downside ratio {ratio}",
        metrics={"upside_downside_ratio": ratio},
    )


def analyze_fragility(snapshot: CompanySnapshot) -> Component:
    latest = snapshot.metrics[0] if snapshot.metrics else None
    debt_to_equity = latest.debt_to_equity if latest else None
    current_ratio = latest.current_ratio if latest else None
    score = 0.0
    if debt_to_equity is not None and debt_to_equity < 0.7:
        score += 1
    if current_ratio is not None and current_ratio >= 1.2:
        score += 1
    return Component(
        name="fragility",
        score=score,
        max_score=2,
        details=f"D/E {debt_to_equity}; current ratio {current_ratio}",
        metrics={"debt_to_equity": debt_to_equity, "current_ratio": current_ratio},
    )


def analyze_skin_in_game(snapshot: CompanySnapshot) -> Component:
    insider = insider_component(snapshot, "skin_in_game")
    score = 1.0 if (insider.score or 0) > 0 else 0.0
    return insider.model_copy(update={"score": score, "max_score": 1})


def analyze_volatility_regime(snapshot: CompanySnapshot) -> Component:
    returns = _returns(snapshot)
    volatility = statistics.pstdev(returns) if len(returns) >= 2 else None
    score = 1.0 if volatility is not None and volatility < 0.03 else 0.0
    return Component(
        name="volatility_regime",
        score=score,
        max_score=1,
        details=f"Daily volatility {volatility}",
        metrics={"volatility": volatility},
    )


def analyze_black_swan_sentinel(snapshot: CompanySnapshot) -> Component:
    negative = sum(item.sentiment == "negative" for item in snapshot.news)
    returns = _returns(snapshot)
    crashes = sum(value <= -0.1 for value in returns)
    score = 1.0 if negative == 0 and crashes == 0 else 0.0
    return Component(
        name="black_swan_sentinel",
        score=score,
        max_score=1,
        details=f"Negative headlines {negative}; crash days {crashes}",
        metrics={"negative_headlines": negative, "crash_days": crashes},
    )


def score_taleb(snapshot: CompanySnapshot) -> QuantScore:
    if not snapshot.prices and not snapshot.metrics and not snapshot.line_items:
        return no_data(Domain.TECHNICALS, snapshot.ticker, "no tail-risk observations")
    components = [
        analyze_tail_risk(snapshot),
        analyze_antifragility(snapshot),
        analyze_convexity(snapshot),
        analyze_fragility(snapshot),
        analyze_skin_in_game(snapshot),
        analyze_volatility_regime(snapshot),
        analyze_black_swan_sentinel(snapshot),
    ]
    total = component_total(components)
    gaps = []
    if len(snapshot.prices) < 5:
        gaps.append("insufficient price history")
    if not snapshot.metrics:
        gaps.append("financial metrics unavailable")
    if not snapshot.line_items:
        gaps.append("annual line items unavailable")
    if not snapshot.insider_trades:
        gaps.append("insider trades unavailable")
    if not snapshot.news:
        gaps.append("company news unavailable")
    return QuantScore(
        analyst=Domain.TECHNICALS,
        ticker=snapshot.ticker,
        signal=score_signal(total, MAX_SCORE),
        confidence=midpoint_confidence(total, MAX_SCORE),
        score=total,
        max_score=MAX_SCORE,
        components=components,
        data_gaps=gaps,
    )
