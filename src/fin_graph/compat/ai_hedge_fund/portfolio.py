"""Pure adapter from deterministic persona findings to v1 portfolio decisions."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from fin_graph.compat.ai_hedge_fund.models import (
    LegacyAnalystSignal,
    LegacyHedgeFundResult,
    LegacyPortfolioDecision,
)
from fin_graph.domain.fills import apply_fill
from fin_graph.domain.orders import (
    OrderAction,
    PortfolioDecision,
    clamp_decision,
    compute_allowed_actions,
    hold,
)
from fin_graph.domain.portfolio import Portfolio
from fin_graph.domain.quant.common import Signal
from fin_graph.domain.quant.risk import (
    FALLBACK_DAILY_VOLATILITY,
    TRADING_DAYS,
    PositionLimit,
    VolatilityMetrics,
    volatility_limit_pct,
)
from fin_graph.graphs.analysis.compat import PersonaFinding

_DIRECTION = {Signal.BULLISH: 1.0, Signal.NEUTRAL: 0.0, Signal.BEARISH: -1.0}


def _position_limit(
    ticker: str, price: float, portfolio: Portfolio, prices: Mapping[str, float]
) -> PositionLimit:
    volatility = VolatilityMetrics(
        daily=FALLBACK_DAILY_VOLATILITY,
        annualized=FALLBACK_DAILY_VOLATILITY * math.sqrt(TRADING_DAYS),
        percentile=100,
        data_points=0,
    )
    if not math.isfinite(price) or price <= 0:
        return PositionLimit(
            ticker=ticker,
            current_price=0,
            remaining_limit=0,
            volatility=volatility,
            error="Missing price data for risk calculation",
        )
    valid_prices = {
        symbol: value for symbol, value in prices.items() if math.isfinite(value) and value > 0
    }
    portfolio_value = max(portfolio.net_liquidation_value(valid_prices), 0.0)
    current_position_value = portfolio.position(ticker).exposure(price)
    limit_pct = volatility_limit_pct(volatility.annualized)
    remaining = min(portfolio_value * limit_pct - current_position_value, portfolio.cash)
    return PositionLimit(
        ticker=ticker,
        current_price=price,
        remaining_limit=remaining,
        volatility=volatility,
        portfolio_value=portfolio_value,
        current_position_value=current_position_value,
        volatility_limit_pct=limit_pct,
        limit_pct=limit_pct,
    )


def _legacy_signal(finding: PersonaFinding) -> LegacyAnalystSignal:
    if finding.score is None:
        return LegacyAnalystSignal(
            signal=Signal.NEUTRAL,
            confidence=0,
            reasoning=finding.error or finding.reasoning or "Analysis unavailable",
        )
    reasoning: dict[str, object] | str
    if finding.reasoning:
        reasoning = finding.reasoning
    else:
        reasoning = {
            "components": [item.model_dump(mode="json") for item in finding.score.components],
            "data_gaps": finding.score.data_gaps,
        }
    return LegacyAnalystSignal(
        signal=finding.score.signal,
        confidence=finding.score.confidence,
        reasoning=reasoning,
    )


def _consensus(findings: Sequence[PersonaFinding]) -> tuple[Signal | None, int]:
    scores = [finding.score for finding in findings if finding.score is not None]
    if not scores:
        return None, 0
    net = sum(_DIRECTION[score.signal] * score.confidence for score in scores) / len(scores)
    if net > 0:
        signal = Signal.BULLISH
    elif net < 0:
        signal = Signal.BEARISH
    else:
        signal = Signal.NEUTRAL
    return signal, min(100, round(abs(net)))


def _legacy_decision(decision: PortfolioDecision) -> LegacyPortfolioDecision:
    return LegacyPortfolioDecision(
        action=decision.action,
        quantity=decision.quantity,
        confidence=min(100, round(decision.confidence)),
        reasoning=decision.reasoning,
    )


def _decide(
    ticker: str,
    price: float,
    findings: Sequence[PersonaFinding],
    portfolio: Portfolio,
    allowed: Mapping[OrderAction, int],
) -> LegacyPortfolioDecision:
    signal, confidence = _consensus(findings)
    if signal is None:
        return _legacy_decision(hold("No analyst signals"))
    if not math.isfinite(price) or price <= 0:
        return _legacy_decision(hold("No valid trade available", confidence=100))
    if signal is Signal.NEUTRAL:
        return _legacy_decision(hold("Weighted analyst consensus is neutral"))
    if set(allowed) == {OrderAction.HOLD}:
        return _legacy_decision(hold("No valid trade available", confidence=100))

    held = portfolio.position(ticker)
    if signal is Signal.BULLISH:
        action = OrderAction.COVER if held.short else OrderAction.BUY
    else:
        action = OrderAction.SELL if held.long else OrderAction.SHORT
    maximum = allowed.get(action)
    proposed = PortfolioDecision(
        action=action,
        quantity=maximum if maximum is not None else 1,
        confidence=confidence,
        reasoning=(f"Weighted analyst consensus: {signal.value} ({confidence} confidence)."),
    )
    safe, _ = clamp_decision(proposed, allowed)
    return _legacy_decision(safe)


def _fill_safe_actions(
    ticker: str,
    price: float,
    portfolio: Portfolio,
    actions: Mapping[OrderAction, int],
) -> dict[OrderAction, int]:
    """Cap allowed quantities to what the immutable paper fill engine can execute now."""

    safe: dict[OrderAction, int] = {OrderAction.HOLD: 0}
    for action, quantity in actions.items():
        if action is OrderAction.HOLD:
            continue
        fill = apply_fill(portfolio, ticker, action, quantity, price, partial=True)
        if fill.executed:
            safe[action] = fill.executed
    return safe


def build_legacy_portfolio_result(
    findings: Sequence[PersonaFinding],
    portfolio: Portfolio,
    prices: Mapping[str, float],
) -> LegacyHedgeFundResult:
    """Aggregate persona scores and enforce v1 risk, cash, shares, and margin bounds."""

    by_ticker: dict[str, list[PersonaFinding]] = {}
    analyst_signals: dict[str, dict[str, LegacyAnalystSignal]] = {}
    seen: set[tuple[str, str]] = set()
    for finding in findings:
        key = (finding.analyst_id.value, finding.ticker)
        if key in seen:
            raise ValueError(f"duplicate finding: {key[0]} / {key[1]}")
        seen.add(key)
        by_ticker.setdefault(finding.ticker, []).append(finding)
        analyst_signals.setdefault(finding.analyst_id.value, {})[finding.ticker] = _legacy_signal(
            finding
        )

    tickers = tuple(dict.fromkeys([*prices, *by_ticker]))
    normalized_prices = {ticker: float(prices.get(ticker, 0.0)) for ticker in tickers}
    execution_prices = {
        ticker: price if math.isfinite(price) and price > 0 else 0.0
        for ticker, price in normalized_prices.items()
    }
    limits = {
        ticker: _position_limit(ticker, normalized_prices[ticker], portfolio, normalized_prices)
        for ticker in tickers
    }
    allowed = compute_allowed_actions(
        tickers,
        execution_prices,
        {ticker: limit.max_shares for ticker, limit in limits.items()},
        portfolio,
    )
    safe_allowed = {
        ticker: _fill_safe_actions(ticker, execution_prices[ticker], portfolio, allowed[ticker])
        for ticker in tickers
    }
    decisions = {
        ticker: _decide(
            ticker,
            normalized_prices[ticker],
            by_ticker.get(ticker, ()),
            portfolio,
            safe_allowed[ticker],
        )
        for ticker in tickers
    }
    return LegacyHedgeFundResult(decisions=decisions, analyst_signals=analyst_signals)
