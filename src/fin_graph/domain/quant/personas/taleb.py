"""Nassim Taleb v1 tail-risk, antifragility, convexity and fragility formulas."""

from __future__ import annotations

import math
import statistics
from itertools import pairwise

from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.quant.common import Component, QuantScore, no_data, score_signal
from fin_graph.domain.quant.personas import midpoint_confidence
from fin_graph.domain.quant.personas._growth_common import line_values
from fin_graph.domain.tools import Domain

MAX_SCORE = 56.0


def _returns(snapshot: CompanySnapshot) -> list[float]:
    closes = [price.close for price in snapshot.prices]
    if any(not math.isfinite(value) for value in closes):
        raise ValueError("non-finite price in tail-risk input")
    if any(value <= 0 for value in closes):
        raise ValueError("non-positive price in tail-risk input")
    return [current / previous - 1 for previous, current in pairwise(closes)]


def _skew(values: list[float]) -> float:
    count = len(values)
    if count < 3:
        return 0.0
    mean, deviation = statistics.fmean(values), statistics.stdev(values)
    return (
        0.0
        if deviation == 0
        else count
        / ((count - 1) * (count - 2))
        * sum(((value - mean) / deviation) ** 3 for value in values)
    )


def _kurtosis(values: list[float]) -> float:
    count = len(values)
    if count < 4:
        return 0.0
    mean, deviation = statistics.fmean(values), statistics.stdev(values)
    if deviation == 0:
        return 0.0
    fourth = sum(((value - mean) / deviation) ** 4 for value in values)
    return count * (count + 1) / ((count - 1) * (count - 2) * (count - 3)) * fourth - 3 * (
        count - 1
    ) ** 2 / ((count - 2) * (count - 3))


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    return (
        ordered[lower]
        if lower == upper
        else ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    )


def _rolling_std(values: list[float], window: int) -> list[float | None]:
    return [
        statistics.stdev(values[index - window + 1 : index + 1]) if index + 1 >= window else None
        for index in range(len(values))
    ]


def analyze_tail_risk(snapshot: CompanySnapshot) -> Component:
    returns = _returns(snapshot)
    if len(snapshot.prices) < 20:
        return Component(name="tail_risk", score=0, max_score=8, metrics={"max_drawdown": None})
    recent = returns[-63:] if len(returns) >= 63 else returns
    kurtosis, skew = _kurtosis(recent), _skew(recent)
    score = 2.0 if kurtosis > 5 else 1.0 if kurtosis > 2 else 0.0
    score += 2 if skew > 0.5 else 1 if skew > -0.5 else 0
    positive, negative = (
        [value for value in returns if value > 0],
        [value for value in returns if value < 0],
    )
    tail_ratio: float | None = None
    if len(positive) > 20 and len(negative) > 20:
        left = abs(_percentile(negative, 0.05))
        tail_ratio = _percentile(positive, 0.95) / left if left > 0 else 1.0
        score += 2 if tail_ratio > 1.2 else 1 if tail_ratio > 0.8 else 0
    peak, max_drawdown = snapshot.prices[0].close, 0.0
    for price in snapshot.prices:
        peak = max(peak, price.close)
        max_drawdown = min(max_drawdown, price.close / peak - 1)
    score += 2 if max_drawdown > -0.15 else 1 if max_drawdown > -0.30 else 0
    return Component(
        name="tail_risk",
        score=score,
        max_score=8,
        metrics={
            "kurtosis": kurtosis,
            "skew": skew,
            "tail_ratio": tail_ratio,
            "max_drawdown": max_drawdown,
        },
    )


def analyze_antifragility(snapshot: CompanySnapshot) -> Component:
    latest = snapshot.line_items[0] if snapshot.line_items else None
    cash = latest.value("cash_and_equivalents") if latest else None
    debt = latest.value("total_debt") if latest else None
    assets = latest.value("total_assets") if latest else None
    score = 0.0
    net_cash = cash - debt if cash is not None and debt is not None else None
    if net_cash is not None:
        score += (
            3
            if net_cash > 0
            and snapshot.market_cap is not None
            and cash is not None
            and cash > 0.2 * snapshot.market_cap
            else 2
            if net_cash > 0
            else 1
            if assets is not None and debt is not None and assets != 0 and debt < 0.3 * assets
            else 0
        )
    latest_metric = snapshot.metrics[0] if snapshot.metrics else None
    leverage = latest_metric.debt_to_equity if latest_metric else None
    if leverage is not None:
        score += 2 if leverage < 0.3 else 1 if leverage < 0.7 else 0
    margins = [
        item.operating_margin for item in snapshot.metrics if item.operating_margin is not None
    ]
    margin_cv: float | None = None
    if len(margins) >= 3:
        mean = statistics.fmean(margins)
        margin_cv = statistics.pstdev(margins) / abs(mean) if mean else math.inf
        score += (
            3
            if margin_cv < 0.15 and mean > 0.15
            else 2
            if margin_cv < 0.30 and mean > 0.10
            else 1
            if margin_cv < 0.30
            else 0
        )
    fcf = line_values(snapshot.line_items, "free_cash_flow")
    positive = sum(value > 0 for value in fcf)
    if fcf:
        score += 2 if positive == len(fcf) else 1 if positive > len(fcf) / 2 else 0
    return Component(
        name="antifragility",
        score=score,
        max_score=10,
        metrics={
            "net_cash": net_cash,
            "debt_to_equity": leverage,
            "margin_cv": margin_cv,
            "positive_fcf_periods": positive,
        },
    )


def analyze_convexity(snapshot: CompanySnapshot) -> Component:
    latest = snapshot.line_items[0] if snapshot.line_items else None
    research = latest.value("research_and_development") if latest else None
    revenue = latest.value("revenue") if latest else None
    ratio = abs(research) / revenue if research is not None and revenue and revenue > 0 else None
    score = (
        3.0
        if ratio is not None and ratio > 0.15
        else 2.0
        if ratio is not None and ratio > 0.08
        else 1.0
        if ratio is not None and ratio > 0.03
        else 0.0
    )
    returns = _returns(snapshot)
    upside, downside = (
        [value for value in returns if value > 0],
        [value for value in returns if value < 0],
    )
    up_down: float | None = None
    if len(snapshot.prices) >= 20 and len(upside) > 10 and len(downside) > 10:
        up_down = statistics.fmean(upside) / abs(statistics.fmean(downside))
        score += 2 if up_down > 1.3 else 1 if up_down > 1 else 0
    cash = latest.value("cash_and_equivalents") if latest else None
    cash_ratio = cash / snapshot.market_cap if cash is not None and snapshot.market_cap else None
    if cash_ratio is not None:
        score += (
            3 if cash_ratio > 0.30 else 2 if cash_ratio > 0.15 else 1 if cash_ratio > 0.05 else 0
        )
    fcf = latest.value("free_cash_flow") if latest else None
    fcf_yield = fcf / snapshot.market_cap if fcf is not None and snapshot.market_cap else None
    if fcf_yield is None and snapshot.metrics:
        fcf_yield = snapshot.metrics[0].free_cash_flow_yield
    if fcf_yield is not None:
        score += 2 if fcf_yield > 0.10 else 1 if fcf_yield > 0.05 else 0
    return Component(
        name="convexity",
        score=score,
        max_score=10,
        metrics={
            "research_ratio": ratio,
            "upside_downside_ratio": up_down,
            "cash_ratio": cash_ratio,
            "free_cash_flow_yield": fcf_yield,
        },
    )


def analyze_fragility(snapshot: CompanySnapshot) -> Component:
    if not snapshot.metrics:
        return Component(name="fragility", score=0, max_score=8)
    latest = snapshot.metrics[0]
    score = 0.0
    if latest.debt_to_equity is not None:
        score += (
            0
            if latest.debt_to_equity > 2
            else 1
            if latest.debt_to_equity > 1
            else 2
            if latest.debt_to_equity > 0.5
            else 3
        )
    if latest.interest_coverage is not None:
        score += 2 if latest.interest_coverage > 10 else 1 if latest.interest_coverage > 5 else 0
    earnings = [
        item.earnings_growth for item in snapshot.metrics if item.earnings_growth is not None
    ]
    earnings_std = statistics.pstdev(earnings) if len(earnings) >= 3 else None
    if earnings_std is not None:
        score += 2 if earnings_std < 0.20 else 1 if earnings_std < 0.50 else 0
    if latest.net_margin is not None and latest.net_margin > 0.15:
        score += 1
    return Component(
        name="fragility",
        score=max(score, 0),
        max_score=8,
        metrics={
            "debt_to_equity": latest.debt_to_equity,
            "interest_coverage": latest.interest_coverage,
            "earnings_growth_stdev": earnings_std,
            "net_margin": latest.net_margin,
        },
    )


def analyze_skin_in_game(snapshot: CompanySnapshot) -> Component:
    if not snapshot.insider_trades:
        return Component(name="skin_in_game", score=1, max_score=4, metrics={"net_shares": 0})
    bought = sum(
        item.transaction_shares or 0
        for item in snapshot.insider_trades
        if (item.transaction_shares or 0) > 0
    )
    sold = abs(
        sum(
            item.transaction_shares or 0
            for item in snapshot.insider_trades
            if (item.transaction_shares or 0) < 0
        )
    )
    net = bought - sold
    ratio = net / max(sold, 1) if net > 0 else None
    score = (
        4.0
        if ratio is not None and ratio > 2
        else 3.0
        if ratio is not None and ratio > 0.5
        else 2.0
        if net > 0
        else 0.0
    )
    return Component(
        name="skin_in_game",
        score=score,
        max_score=4,
        metrics={"bought": bought, "sold": sold, "net_shares": net, "buy_sell_ratio": ratio},
    )


def analyze_volatility_regime(snapshot: CompanySnapshot) -> Component:
    returns = _returns(snapshot)
    if len(snapshot.prices) < 30:
        return Component(name="volatility_regime", score=0, max_score=6)
    annualized = [
        value * math.sqrt(252) if value is not None else None for value in _rolling_std(returns, 21)
    ]
    clean = [value for value in annualized if value is not None]
    if len(clean) < 21:
        return Component(name="volatility_regime", score=0, max_score=6)
    current, average = clean[-1], statistics.fmean(clean[-63:] if len(clean) >= 63 else clean)
    regime = current / average if average > 0 else 1.0
    score = (
        0.0
        if regime < 0.7
        else 1.0
        if regime < 0.9
        else 3.0
        if regime <= 1.3
        else 4.0
        if regime <= 2
        else 2.0
    )
    current_vov: float | None = None
    median_vov: float | None = None
    if len(clean) >= 42:
        vol_of_vol = [value for value in _rolling_std(clean, 21) if value is not None]
        if vol_of_vol:
            current_vov, median_vov = vol_of_vol[-1], statistics.median(vol_of_vol)
            if median_vov > 0:
                score += 2 if current_vov > 2 * median_vov else 1 if current_vov > median_vov else 0
    return Component(
        name="volatility_regime",
        score=score,
        max_score=6,
        metrics={
            "current_volatility": current,
            "average_volatility": average,
            "regime": regime,
            "vol_of_vol": current_vov,
            "median_vol_of_vol": median_vov,
        },
    )


def analyze_black_swan_sentinel(snapshot: CompanySnapshot) -> Component:
    negative = sum(item.sentiment == "negative" for item in snapshot.news)
    negative_ratio = negative / len(snapshot.news) if snapshot.news else 0.0
    volumes = [item.volume for item in snapshot.prices]
    recent_volume = statistics.fmean(volumes[-5:]) if len(volumes) >= 10 else 0
    average_volume = (
        statistics.fmean(volumes[-63:] if len(volumes) >= 63 else volumes) if volumes else 0
    )
    volume_spike = recent_volume / average_volume if average_volume > 0 else 1.0
    recent_return = (
        snapshot.prices[-1].close / snapshot.prices[-5].close - 1
        if len(snapshot.prices) >= 5
        else 0.0
    )
    score = 2.0
    if negative_ratio > 0.7 and volume_spike > 2:
        score = 0
    elif (
        negative_ratio > 0.5
        or volume_spike > 2.5
        or (negative_ratio > 0.3 and abs(recent_return) > 0.10)
    ):
        score = 1
    elif negative_ratio < 0.3 and volume_spike < 1.5:
        score = 3
    if negative_ratio > 0.4 and volume_spike < 1.5 and score < 4:
        score = min(score + 1, 4)
    return Component(
        name="black_swan_sentinel",
        score=score,
        max_score=4,
        metrics={
            "negative_news_ratio": negative_ratio,
            "volume_spike": volume_spike,
            "recent_return": recent_return,
        },
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
    total = sum(component.score or 0 for component in components)
    gaps = []
    if len(snapshot.prices) < 20:
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
