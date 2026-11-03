"""V2 signal consensus and sizing over the native portfolio/order models."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from fin_graph.domain.orders import OrderAction, ProposedOrder, client_order_id
from fin_graph.domain.portfolio import Portfolio
from fin_graph.quant.signals.base import Signal
from fin_graph.safety.trading_limits import TradingLimits

_BULLISH = 0.15
_BEARISH = -0.15


def combine_signals(
    signals: Sequence[Signal], weights: Mapping[str, float] | None = None
) -> Signal:
    """Blend same-ticker/as-of views with explicit non-negative model weights."""

    if not signals:
        raise ValueError("at least one signal is required")
    ticker = signals[0].ticker
    as_of = signals[0].as_of
    if any(item.ticker != ticker or item.as_of != as_of for item in signals):
        raise ValueError("signals must share ticker and as_of")
    chosen = {
        item.model_name: (1.0 if weights is None else float(weights.get(item.model_name, 0)))
        for item in signals
    }
    if any(value < 0 for value in chosen.values()):
        raise ValueError("weights must be non-negative")
    total = sum(chosen.values())
    if total <= 0:
        raise ValueError("sum of signal weights must be positive")
    value = sum(item.value * chosen[item.model_name] for item in signals) / total
    bullish = sum(item.value > _BULLISH for item in signals)
    bearish = sum(item.value < _BEARISH for item in signals)
    return Signal(
        model_name="consensus",
        ticker=ticker,
        as_of=as_of,
        value=value,
        reasoning="; ".join(
            f"{item.model_name}={item.value:+.3f} (w={chosen[item.model_name]:g})"
            for item in signals
        ),
        components={item.model_name: item.value for item in signals},
        metadata={
            "weights": chosen,
            "bullish_count": bullish,
            "bearish_count": bearish,
            "neutral_count": len(signals) - bullish - bearish,
        },
    )


def size_from_signal(
    signal: Signal,
    portfolio: Portfolio,
    price: float,
    limits: TradingLimits,
    *,
    min_confidence: float = 0.65,
    run_id: str = "v2-signal",
) -> ProposedOrder | None:
    """Turn a sufficiently strong view into a native, idempotent paper order."""

    if price <= 0 or abs(signal.value) < min_confidence or limits.kill_switch:
        return None
    held = portfolio.position(signal.ticker)
    nlv = portfolio.net_liquidation_value({signal.ticker: price})
    notional_cap = int(limits.max_order_notional // price)
    position_cap = int(max(nlv, 0) * limits.max_position_pct // price)
    action: OrderAction
    quantity: int
    if signal.value > 0:
        if held.short:
            action, quantity = OrderAction.COVER, held.short
        else:
            action = OrderAction.BUY
            quantity = min(
                notional_cap,
                max(0, position_cap - held.long),
                int(portfolio.cash // price),
            )
    elif held.long:
        action, quantity = OrderAction.SELL, max(1, held.long // 2)
    elif limits.short_selling_enabled:
        action = OrderAction.SHORT
        collateral = price * portfolio.margin_requirement
        affordable = int(portfolio.cash // collateral) if collateral > 0 else notional_cap
        quantity = min(notional_cap, position_cap, affordable)
    else:
        return None
    if quantity <= 0 or not limits.permits(action):
        return None
    return ProposedOrder(
        client_order_id=client_order_id(run_id, signal.ticker, action, quantity, signal.as_of),
        ticker=signal.ticker,
        action=action,
        quantity=quantity,
        reference_price=price,
        as_of=signal.as_of,
        confidence=abs(signal.value) * 100,
        reasoning=signal.reasoning,
        thesis_category=signal.model_name,
    )
