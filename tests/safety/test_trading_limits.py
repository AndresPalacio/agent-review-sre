from __future__ import annotations

from datetime import date

import pytest

from fin_graph.config.settings import Settings
from fin_graph.domain.orders import OrderAction, ProposedOrder
from fin_graph.domain.portfolio import Portfolio, Position
from fin_graph.safety.trading_limits import (
    TradingLimits,
    check_order,
    limit_allowed_actions,
)

PRICES = {"AAPL": 100.0}
PORTFOLIO = Portfolio(cash=50_000.0, positions={"AAPL": Position(long=50)})


def _order(action: OrderAction, quantity: int) -> ProposedOrder:
    return ProposedOrder(
        client_order_id="ord_1",
        ticker="AAPL",
        action=action,
        quantity=quantity,
        reference_price=100.0,
        as_of=date(2024, 3, 8),
    )


def test_limits_come_from_settings() -> None:
    settings = Settings(
        _env_file=None,
        trading_max_order_notional=5_000,
        trading_allowed_actions=("buy", "sell", "hold"),
        trading_short_selling_enabled=True,
        trading_kill_switch=True,
    )

    limits = TradingLimits.from_settings(settings)

    assert limits.max_order_notional == 5_000
    assert limits.allowed_actions == {OrderAction.BUY, OrderAction.SELL}
    assert limits.kill_switch and not limits.permits(OrderAction.BUY)


def test_allowed_actions_are_pruned_by_notional_position_and_shorting() -> None:
    allowed = {
        OrderAction.HOLD: 0,
        OrderAction.BUY: 400,
        OrderAction.SELL: 50,
        OrderAction.SHORT: 300,
    }
    limits = TradingLimits(max_order_notional=10_000, max_position_pct=0.25)

    pruned = limit_allowed_actions(
        allowed, ticker="AAPL", price=100.0, portfolio=PORTFOLIO, prices=PRICES, limits=limits
    )

    # NLV 55,000 → 25% is 137 shares; 50 already held leaves 87 to buy. Shorting is off.
    assert pruned == {OrderAction.HOLD: 0, OrderAction.BUY: 87, OrderAction.SELL: 50}


def test_kill_switch_and_daily_cap_leave_only_hold() -> None:
    allowed = {OrderAction.HOLD: 0, OrderAction.BUY: 10}
    kwargs = {"ticker": "AAPL", "price": 100.0, "portfolio": PORTFOLIO, "prices": PRICES}

    assert limit_allowed_actions(allowed, limits=TradingLimits(kill_switch=True), **kwargs) == {
        OrderAction.HOLD: 0
    }
    assert limit_allowed_actions(
        allowed, limits=TradingLimits(max_daily_orders=2), orders_today=2, **kwargs
    ) == {OrderAction.HOLD: 0}


@pytest.mark.parametrize(
    ("action", "quantity", "limits", "rules", "largest"),
    [
        (OrderAction.BUY, 50, TradingLimits(), [], 87),
        (OrderAction.BUY, 200, TradingLimits(), ["max_order_notional", "max_position_pct"], 87),
        (OrderAction.SELL, 80, TradingLimits(), ["exceeds_position"], 50),
        (OrderAction.SHORT, 10, TradingLimits(), ["short_selling_disabled"], 0),
        (OrderAction.COVER, 1, TradingLimits(), ["exceeds_position"], 0),
        (OrderAction.BUY, 10, TradingLimits(kill_switch=True), ["kill_switch"], 0),
        (
            OrderAction.BUY,
            10,
            TradingLimits(allowed_actions=frozenset({OrderAction.SELL})),
            ["action_not_allowed"],
            0,
        ),
    ],
)
def test_check_order_names_every_broken_rule(
    action: OrderAction, quantity: int, limits: TradingLimits, rules: list[str], largest: int
) -> None:
    check = check_order(_order(action, quantity), PORTFOLIO, PRICES, limits=limits, orders_today=0)

    assert [item.rule for item in check.violations] == rules
    assert check.allowed is (not rules)
    assert check.max_quantity == largest


def test_buying_more_than_the_cash_is_refused() -> None:
    poor = Portfolio(cash=500.0)

    check = check_order(
        _order(OrderAction.BUY, 10), poor, PRICES, limits=TradingLimits(), orders_today=0
    )

    assert "insufficient_cash" in {item.rule for item in check.violations}
    assert check.max_quantity == 1
