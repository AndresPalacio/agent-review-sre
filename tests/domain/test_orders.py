from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from fin_graph.domain.orders import (
    OrderAction,
    PortfolioDecision,
    clamp_decision,
    client_order_id,
    compute_allowed_actions,
    hold,
    propose_order,
)
from fin_graph.domain.portfolio import Portfolio, Position

AS_OF = date(2024, 3, 8)
ALLOWED = {OrderAction.HOLD: 0, OrderAction.BUY: 20, OrderAction.SELL: 50}


def test_clamp_keeps_the_model_inside_the_allowed_actions() -> None:
    too_many = PortfolioDecision(action=OrderAction.BUY, quantity=500, confidence=70)
    forbidden = PortfolioDecision(action=OrderAction.SHORT, quantity=5)
    empty = PortfolioDecision(action=OrderAction.SELL, quantity=0)

    assert clamp_decision(too_many, ALLOWED) == (
        too_many.model_copy(update={"quantity": 20}),
        "quantity 500 cut to the maximum 20",
    )
    safe, note = clamp_decision(forbidden, ALLOWED)
    assert safe.action is OrderAction.HOLD and note == "short not allowed"
    assert clamp_decision(empty, ALLOWED)[0].action is OrderAction.HOLD
    assert clamp_decision(hold("wait"), ALLOWED) == (hold("wait"), None)


def test_orders_have_stable_ids_and_an_inverse() -> None:
    decision = PortfolioDecision(
        action=OrderAction.BUY, quantity=10, confidence=65, reasoning="cheap"
    )

    order = propose_order(decision, ticker="AAPL", price=170.0, as_of=AS_OF, run_id="run-1")

    assert order is not None
    assert order.client_order_id == client_order_id("run-1", "AAPL", OrderAction.BUY, 10, AS_OF)
    assert order.notional == 1700.0 and order.side == "long"
    rollback = order.inverse()
    assert rollback.action is OrderAction.SELL and rollback.client_order_id.endswith("-rollback")
    assert propose_order(hold("no"), ticker="AAPL", price=170.0, as_of=AS_OF, run_id="r") is None


def test_allowed_actions_without_cash_or_limit() -> None:
    portfolio = Portfolio(cash=0.0, positions={"AAPL": Position(long=3)})

    allowed = compute_allowed_actions(
        ["AAPL"], {"AAPL": 100.0}, {"AAPL": 5}, portfolio, allow_short=False
    )

    assert allowed["AAPL"] == {OrderAction.HOLD: 0, OrderAction.SELL: 3}


def test_net_liquidation_value_counts_longs_and_shorts() -> None:
    portfolio = Portfolio(
        cash=1_000.0,
        positions={"A": Position(long=10), "B": Position(short=4), "C": Position(long=1)},
    )

    assert portfolio.net_liquidation_value({"A": 10.0, "B": 50.0}) == 1_000 + 100 - 200
    assert portfolio.active_tickers() == {"A", "B", "C"}


def test_decisions_reject_negative_quantities() -> None:
    with pytest.raises(ValidationError):
        PortfolioDecision(action=OrderAction.BUY, quantity=-1)
