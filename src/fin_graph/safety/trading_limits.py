"""Hard trading gates every paper order passes before it can even be proposed for approval.

The model chooses among allowed actions; these limits decide what is allowed. They are checked
twice: :func:`limit_allowed_actions` prunes what the portfolio step may offer the model, and
:func:`check_order` re-checks the concrete order at execution time, when cash, positions or the
day's order count may have changed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from fin_graph.config.settings import Settings
from fin_graph.domain.orders import OrderAction, ProposedOrder
from fin_graph.domain.portfolio import Portfolio

TRADE_ACTIONS = frozenset(action for action in OrderAction if action is not OrderAction.HOLD)
#: Actions that add exposure; the others only reduce it.
RISK_INCREASING = frozenset({OrderAction.BUY, OrderAction.SHORT})


@dataclass(frozen=True)
class TradingLimits:
    """The gates, from ``TRADING_*`` settings; the short rule never blocks covering a short."""

    max_order_notional: float = 10_000.0
    max_position_pct: float = 0.25
    max_daily_orders: int = 10
    short_selling_enabled: bool = False
    allowed_actions: frozenset[OrderAction] = field(default=TRADE_ACTIONS)
    kill_switch: bool = False

    @classmethod
    def from_settings(cls, settings: Settings) -> TradingLimits:
        actions = frozenset(OrderAction(item) for item in settings.trading_allowed_actions)
        return cls(
            max_order_notional=settings.trading_max_order_notional,
            max_position_pct=settings.trading_max_position_pct,
            max_daily_orders=settings.trading_max_daily_orders,
            short_selling_enabled=settings.trading_short_selling_enabled,
            allowed_actions=(actions - {OrderAction.HOLD}) or TRADE_ACTIONS,
            kill_switch=settings.trading_kill_switch,
        )

    def permits(self, action: OrderAction) -> bool:
        """Whether ``action`` may be traded at all under these limits."""
        if action is OrderAction.HOLD:
            return True
        if self.kill_switch or action not in self.allowed_actions:
            return False
        return action is not OrderAction.SHORT or self.short_selling_enabled


class LimitViolation(BaseModel):
    """One rule an order breaks."""

    rule: str
    detail: str


class LimitCheck(BaseModel):
    """Whether the order passes, every rule it breaks, and the largest quantity that would pass."""

    client_order_id: str
    allowed: bool
    violations: list[LimitViolation] = Field(default_factory=list)
    max_quantity: int = 0


def _max_position_shares(
    action: OrderAction, price: float, portfolio: Portfolio, ticker: str, nlv: float, pct: float
) -> int:
    """Shares ``action`` may add before the position passes ``pct`` of net liquidation value."""
    if action not in RISK_INCREASING or price <= 0 or nlv <= 0:
        return 2**31
    held = portfolio.position(ticker).net_shares
    cap = int(pct * nlv // price)
    if action is OrderAction.BUY:
        return max(0, cap - held)
    return max(0, cap + held)


def limit_allowed_actions(
    allowed: Mapping[OrderAction, int],
    *,
    ticker: str,
    price: float,
    portfolio: Portfolio,
    prices: Mapping[str, float],
    limits: TradingLimits,
    orders_today: int = 0,
) -> dict[OrderAction, int]:
    """Prune ``compute_allowed_actions`` output to what the limits let through; hold stays."""
    if limits.kill_switch or orders_today >= limits.max_daily_orders or price <= 0:
        return {OrderAction.HOLD: 0}
    nlv = portfolio.net_liquidation_value(prices)
    by_notional = int(limits.max_order_notional // price)
    pruned: dict[OrderAction, int] = {OrderAction.HOLD: 0}
    for action, quantity in allowed.items():
        if action is OrderAction.HOLD or not limits.permits(action):
            continue
        cap = min(
            quantity,
            by_notional,
            _max_position_shares(action, price, portfolio, ticker, nlv, limits.max_position_pct),
        )
        if cap > 0:
            pruned[action] = cap
    return pruned


def check_order(
    order: ProposedOrder,
    portfolio: Portfolio,
    prices: Mapping[str, float],
    *,
    limits: TradingLimits,
    orders_today: int,
) -> LimitCheck:
    """Every rule ``order`` breaks right now; execution must refuse an order that is not allowed."""
    violations: list[LimitViolation] = []
    action, price = order.action, prices.get(order.ticker, order.reference_price)
    held = portfolio.position(order.ticker)
    if limits.kill_switch:
        violations.append(LimitViolation(rule="kill_switch", detail="trading is halted"))
    if action is OrderAction.HOLD:
        violations.append(LimitViolation(rule="not_an_order", detail="hold changes nothing"))
    elif action not in limits.allowed_actions:
        violations.append(
            LimitViolation(rule="action_not_allowed", detail=f"{action.value} is not enabled")
        )
    if action is OrderAction.SHORT and not limits.short_selling_enabled:
        violations.append(LimitViolation(rule="short_selling_disabled", detail="no new shorts"))
    if orders_today >= limits.max_daily_orders:
        violations.append(
            LimitViolation(
                rule="max_daily_orders",
                detail=f"{orders_today} orders today, limit {limits.max_daily_orders}",
            )
        )
    notional = order.quantity * price
    if notional > limits.max_order_notional:
        violations.append(
            LimitViolation(
                rule="max_order_notional",
                detail=f"{notional:,.2f} exceeds {limits.max_order_notional:,.2f}",
            )
        )
    if action is OrderAction.SELL and order.quantity > held.long:
        violations.append(
            LimitViolation(
                rule="exceeds_position", detail=f"sells {order.quantity}, holds {held.long}"
            )
        )
    if action is OrderAction.COVER and order.quantity > held.short:
        violations.append(
            LimitViolation(
                rule="exceeds_position", detail=f"covers {order.quantity}, short {held.short}"
            )
        )
    if action is OrderAction.BUY and notional > portfolio.cash:
        violations.append(
            LimitViolation(
                rule="insufficient_cash", detail=f"needs {notional:,.2f}, has {portfolio.cash:,.2f}"
            )
        )
    nlv = portfolio.net_liquidation_value(prices)
    position_cap = _max_position_shares(
        action, price, portfolio, order.ticker, nlv, limits.max_position_pct
    )
    if order.quantity > position_cap:
        violations.append(
            LimitViolation(
                rule="max_position_pct",
                detail=f"the position would exceed {limits.max_position_pct:.0%} of the portfolio",
            )
        )
    largest = min(
        int(limits.max_order_notional // price) if price > 0 else 0,
        position_cap,
        held.long if action is OrderAction.SELL else 2**31,
        held.short if action is OrderAction.COVER else 2**31,
        int(portfolio.cash // price) if action is OrderAction.BUY and price > 0 else 2**31,
    )
    blocked = {"kill_switch", "not_an_order", "action_not_allowed", "short_selling_disabled"}
    if any(item.rule in blocked | {"max_daily_orders"} for item in violations):
        largest = 0
    return LimitCheck(
        client_order_id=order.client_order_id,
        allowed=not violations,
        violations=violations,
        max_quantity=max(0, largest),
    )
