"""Peter Lynch v1 weighted GARP scorer on the reference 0-10 scales."""

from __future__ import annotations

from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.quant.common import Component, QuantScore, Signal, no_data
from fin_graph.domain.quant.personas import midpoint_confidence
from fin_graph.domain.quant.personas._growth_common import line_values
from fin_graph.domain.tools import Domain


def _growth(latest: float, oldest: float) -> float | None:
    return (latest - oldest) / abs(oldest) if abs(oldest) > 1e-9 else None


def analyze_lynch_growth(snapshot: CompanySnapshot) -> Component:
    revenue = line_values(snapshot.line_items, "revenue")
    eps = line_values(snapshot.line_items, "earnings_per_share")
    raw = 0.0
    metrics: dict[str, float | None] = {}
    for name, values in (("revenue_growth", revenue), ("eps_growth", eps)):
        value = _growth(values[0], values[-1]) if len(values) >= 2 else None
        metrics[name] = value
        if value is not None:
            raw += 3 if value > 0.25 else 2 if value > 0.10 else 1 if value > 0.02 else 0
    return Component(
        name="lynch_growth", score=min(10, raw / 6 * 10), max_score=10, metrics=metrics
    )


def analyze_lynch_fundamentals(snapshot: CompanySnapshot) -> Component:
    debt = line_values(snapshot.line_items, "total_debt")
    equity = line_values(snapshot.line_items, "shareholders_equity")
    margins = line_values(snapshot.line_items, "operating_margin")
    fcf = line_values(snapshot.line_items, "free_cash_flow")
    raw = 0.0
    debt_to_equity = (
        debt[0] / (equity[0] or 1e-9) if debt and equity and len(debt) == len(equity) else None
    )
    if debt_to_equity is not None:
        raw += 2 if debt_to_equity < 0.5 else 1 if debt_to_equity < 1 else 0
    margin = margins[0] if margins else None
    if margin is not None:
        raw += 2 if margin > 0.20 else 1 if margin > 0.10 else 0
    latest_fcf = fcf[0] if fcf else None
    raw += 2 if latest_fcf is not None and latest_fcf > 0 else 0
    return Component(
        name="lynch_fundamentals",
        score=min(10, raw / 6 * 10),
        max_score=10,
        metrics={
            "debt_to_equity": debt_to_equity,
            "operating_margin": margin,
            "free_cash_flow": latest_fcf,
        },
    )


def analyze_lynch_valuation(snapshot: CompanySnapshot) -> Component:
    incomes = line_values(snapshot.line_items, "net_income")
    eps = line_values(snapshot.line_items, "earnings_per_share")
    pe = (
        snapshot.market_cap / incomes[0]
        if snapshot.market_cap and incomes and incomes[0] > 0
        else None
    )
    eps_growth: float | None = None
    if len(eps) >= 2 and eps[-1] > 0:
        years = len(eps) - 1
        eps_growth = (
            (eps[0] / eps[-1]) ** (1 / years) - 1
            if eps[0] > 0
            else (eps[0] - eps[-1]) / (eps[-1] * years)
        )
    peg = pe / (eps_growth * 100) if pe and eps_growth and eps_growth > 0 else None
    raw = 0.0
    if pe is not None:
        raw += 2 if pe < 15 else 1 if pe < 25 else 0
    if peg is not None:
        raw += 3 if peg < 1 else 2 if peg < 2 else 1 if peg < 3 else 0
    return Component(
        name="lynch_valuation",
        score=min(10, raw / 5 * 10),
        max_score=10,
        metrics={"price_to_earnings": pe, "eps_growth_rate": eps_growth, "peg_ratio": peg},
    )


def analyze_sentiment(snapshot: CompanySnapshot) -> Component:
    if not snapshot.news:
        return Component(name="sentiment", score=5, max_score=10, metrics={"negative": 0})
    negative_words = (
        "lawsuit",
        "fraud",
        "negative",
        "downturn",
        "decline",
        "investigation",
        "recall",
    )
    negative = sum(
        any(word in item.title.lower() for word in negative_words) for item in snapshot.news
    )
    score = 3.0 if negative > len(snapshot.news) * 0.3 else 6.0 if negative else 8.0
    return Component(
        name="sentiment",
        score=score,
        max_score=10,
        metrics={"negative": negative, "headlines": len(snapshot.news)},
    )


def analyze_insider_activity(snapshot: CompanySnapshot) -> Component:
    buys = sum((item.transaction_shares or 0) > 0 for item in snapshot.insider_trades)
    sells = sum((item.transaction_shares or 0) < 0 for item in snapshot.insider_trades)
    total = buys + sells
    ratio = buys / total if total else None
    score = 5.0 if ratio is None else 8.0 if ratio > 0.7 else 6.0 if ratio > 0.4 else 4.0
    return Component(
        name="insider_activity",
        score=score,
        max_score=10,
        metrics={"buys": buys, "sells": sells, "buy_ratio": ratio},
    )


def score_lynch(snapshot: CompanySnapshot) -> QuantScore:
    if not snapshot.line_items:
        return no_data(Domain.FUNDAMENTALS, snapshot.ticker, "financial line items unavailable")
    components = [
        analyze_lynch_growth(snapshot),
        analyze_lynch_valuation(snapshot),
        analyze_lynch_fundamentals(snapshot),
        analyze_sentiment(snapshot),
        analyze_insider_activity(snapshot),
    ]
    weights = (0.30, 0.25, 0.20, 0.15, 0.10)
    total = sum(
        (component.score or 0) * weight
        for component, weight in zip(components, weights, strict=True)
    )
    signal = Signal.BULLISH if total >= 7.5 else Signal.BEARISH if total <= 4.5 else Signal.NEUTRAL
    gaps = []
    if not snapshot.news:
        gaps.append("company news unavailable")
    if not snapshot.insider_trades:
        gaps.append("insider trades unavailable")
    return QuantScore(
        analyst=Domain.FUNDAMENTALS,
        ticker=snapshot.ticker,
        signal=signal,
        confidence=midpoint_confidence(total, 10),
        score=total,
        max_score=10,
        components=components,
        data_gaps=gaps,
    )
