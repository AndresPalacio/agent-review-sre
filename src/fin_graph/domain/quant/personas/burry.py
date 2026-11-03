"""Michael Burry-style deep-value, balance-sheet and contrarian scoring."""

from __future__ import annotations

from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.quant.common import Component, QuantScore, no_data, score_signal
from fin_graph.domain.quant.personas import component_total, midpoint_confidence
from fin_graph.domain.tools import Domain

MAX_SCORE = 12.0


def _analyze_value(snapshot: CompanySnapshot) -> Component:
    latest = snapshot.line_items[0] if snapshot.line_items else None
    cash_flow = latest.value("free_cash_flow") if latest else None
    cash_flow_yield = (
        cash_flow / snapshot.market_cap if cash_flow is not None and snapshot.market_cap else None
    )
    score = 0.0
    if cash_flow_yield is not None:
        if cash_flow_yield >= 0.15:
            score += 4
        elif cash_flow_yield >= 0.12:
            score += 3
        elif cash_flow_yield >= 0.08:
            score += 2
    ev_to_ebit = getattr(snapshot.metrics[0], "ev_to_ebit", None) if snapshot.metrics else None
    if ev_to_ebit is not None:
        score += 2 if ev_to_ebit < 6 else 1 if ev_to_ebit < 10 else 0
    return Component(
        name="value",
        score=score,
        max_score=6,
        details=f"FCF yield {cash_flow_yield}; EV/EBIT {ev_to_ebit}",
        metrics={"free_cash_flow_yield": cash_flow_yield, "ev_to_ebit": ev_to_ebit},
    )


def _analyze_balance_sheet(snapshot: CompanySnapshot) -> Component:
    latest_metric = snapshot.metrics[0] if snapshot.metrics else None
    latest_item = snapshot.line_items[0] if snapshot.line_items else None
    debt_to_equity = latest_metric.debt_to_equity if latest_metric else None
    score = 0.0
    if debt_to_equity is not None:
        score += 2 if debt_to_equity < 0.5 else 1 if debt_to_equity < 1 else 0
    cash = latest_item.value("cash_and_equivalents") if latest_item else None
    debt = latest_item.value("total_debt") if latest_item else None
    if cash is not None and debt is not None and cash > debt:
        score += 1
    return Component(
        name="balance_sheet",
        score=score,
        max_score=3,
        details=f"D/E {debt_to_equity}; cash {cash}; debt {debt}",
        metrics={"debt_to_equity": debt_to_equity, "cash": cash, "debt": debt},
    )


def _analyze_insider_activity(snapshot: CompanySnapshot) -> Component:
    bought = sum(
        trade.transaction_shares or 0
        for trade in snapshot.insider_trades
        if (trade.transaction_shares or 0) > 0
    )
    sold = abs(
        sum(
            trade.transaction_shares or 0
            for trade in snapshot.insider_trades
            if (trade.transaction_shares or 0) < 0
        )
    )
    net = bought - sold
    score = 2.0 if net > 0 and net / max(sold, 1) > 1 else 1.0 if net > 0 else 0.0
    return Component(
        name="insider_activity",
        score=score,
        max_score=2,
        details=f"Net insider shares {net:,.0f}",
        metrics={"shares_bought": bought, "shares_sold": sold, "net_shares": net},
    )


def _analyze_contrarian_sentiment(snapshot: CompanySnapshot) -> Component:
    negative = sum(item.sentiment in {"negative", "bearish"} for item in snapshot.news)
    return Component(
        name="contrarian_sentiment",
        score=1.0 if negative >= 5 else 0.0,
        max_score=1,
        details=f"{negative} negative headlines",
        metrics={"negative_headlines": negative},
    )


def score_burry(snapshot: CompanySnapshot) -> QuantScore:
    if not snapshot.metrics and not snapshot.line_items:
        return no_data(Domain.VALUATION, snapshot.ticker, "no financial observations")
    components = [
        _analyze_value(snapshot),
        _analyze_balance_sheet(snapshot),
        _analyze_insider_activity(snapshot),
        _analyze_contrarian_sentiment(snapshot),
    ]
    total = component_total(components)
    gaps = []
    if not snapshot.line_items:
        gaps.append("annual line items unavailable")
    if snapshot.market_cap is None:
        gaps.append("market cap unavailable")
    if not snapshot.insider_trades:
        gaps.append("insider trades unavailable")
    if not snapshot.news:
        gaps.append("company news unavailable")
    return QuantScore(
        analyst=Domain.VALUATION,
        ticker=snapshot.ticker,
        signal=score_signal(total, MAX_SCORE),
        confidence=midpoint_confidence(total, MAX_SCORE),
        score=total,
        max_score=MAX_SCORE,
        components=components,
        data_gaps=gaps,
    )
