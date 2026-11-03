from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from fin_graph.domain.fills import InsufficientFunds, apply_fill
from fin_graph.domain.market_hours import is_market_open, is_trading_day, next_open, nyse_holidays
from fin_graph.domain.orders import OrderAction, compute_allowed_actions
from fin_graph.domain.portfolio import Portfolio, Position


def test_long_trades_average_the_cost_and_realize_the_gain() -> None:
    start = Portfolio(cash=10_000.0)

    first = apply_fill(start, "A", OrderAction.BUY, 10, 100.0)
    second = apply_fill(first.portfolio, "A", OrderAction.BUY, 10, 120.0)
    sold = apply_fill(second.portfolio, "A", OrderAction.SELL, 5, 130.0)

    assert second.portfolio.cash == 7_800.0
    assert second.portfolio.position("A") == Position(long=20, long_cost_basis=110.0)
    assert sold.realized_pnl == 100.0 and sold.executed == 5
    assert sold.portfolio.cash == 8_450.0
    assert sold.portfolio.position("A").long_cost_basis == 110.0


def test_shorts_lock_collateral_that_still_counts_as_equity() -> None:
    held = Portfolio(cash=8_450.0, positions={"A": Position(long=15, long_cost_basis=110.0)})
    start = held.model_copy(update={"margin_requirement": 0.5})
    prices = {"A": 130.0, "B": 50.0}
    before = start.net_liquidation_value(prices)

    shorted = apply_fill(start, "B", OrderAction.SHORT, 10, 50.0)

    assert shorted.portfolio.cash == 8_700.0 and shorted.portfolio.margin_used == 250.0
    assert shorted.portfolio.position("B").short_margin_used == 250.0
    # Opening a short at the market price leaves the net liquidation value unchanged.
    assert shorted.portfolio.net_liquidation_value(prices) == before == 10_400.0
    covered = apply_fill(shorted.portfolio, "B", OrderAction.COVER, 4, 40.0)
    assert covered.realized_pnl == 40.0
    assert covered.portfolio.cash == 8_640.0 and covered.portfolio.margin_used == 150.0
    assert covered.portfolio.position("B").short_margin_used == 150.0
    assert covered.portfolio.realized_gains["B"].short == 40.0
    # 10 000 start + 100 long realized + 300 long open + 40 short realized + 60 short open.
    assert covered.portfolio.net_liquidation_value({"A": 130.0, "B": 40.0}) == 10_500.0


def test_closing_a_position_removes_it_and_resets_its_cost() -> None:
    bought = apply_fill(Portfolio(cash=1_000.0), "A", OrderAction.BUY, 5, 100.0)

    sold = apply_fill(bought.portfolio, "A", OrderAction.SELL, 5, 90.0)

    assert "A" not in sold.portfolio.positions and sold.realized_pnl == -50.0
    assert sold.portfolio.cash == 950.0
    assert sold.portfolio.realized_gains["A"].long == -50.0


def test_cover_releases_the_positions_actual_margin_after_mixed_short_entries() -> None:
    first = apply_fill(Portfolio(cash=10_000.0), "A", OrderAction.SHORT, 10, 100.0)
    second = apply_fill(first.portfolio, "A", OrderAction.SHORT, 10, 200.0)

    covered = apply_fill(second.portfolio, "A", OrderAction.COVER, 10, 150.0)

    assert covered.portfolio.position("A").short_margin_used == 750.0
    assert covered.portfolio.margin_used == 750.0
    assert covered.portfolio.realized_gains["A"].short == 0.0


def test_an_approved_quantity_is_never_silently_reduced() -> None:
    portfolio = Portfolio(cash=500.0, positions={"A": Position(long=2)}, margin_requirement=0.5)

    for action, quantity in (
        (OrderAction.BUY, 6),
        (OrderAction.SELL, 3),
        (OrderAction.SHORT, 11),
        (OrderAction.COVER, 1),
    ):
        with pytest.raises(InsufficientFunds):
            apply_fill(portfolio, "A", action, quantity, 100.0)
    partial = apply_fill(portfolio, "A", OrderAction.BUY, 6, 100.0, partial=True)
    assert partial.executed == 5 and partial.portfolio.cash == 0.0


def test_hold_and_empty_orders_change_nothing() -> None:
    portfolio = Portfolio(cash=1_000.0)

    for action, quantity, price in ((OrderAction.HOLD, 5, 10.0), (OrderAction.BUY, 0, 10.0)):
        result = apply_fill(portfolio, "A", action, quantity, price)
        assert result.executed == 0 and result.portfolio == portfolio


def test_short_capacity_matches_the_margin_the_fill_requires() -> None:
    portfolio = Portfolio(cash=1_000.0, margin_requirement=0.5)

    allowed = compute_allowed_actions(["A"], {"A": 100.0}, {"A": 50}, portfolio)

    assert allowed["A"][OrderAction.SHORT] == 20
    apply_fill(portfolio, "A", OrderAction.SHORT, 20, 100.0)
    with pytest.raises(InsufficientFunds):
        apply_fill(portfolio, "A", OrderAction.SHORT, 21, 100.0)


def test_the_nyse_calendar_closes_on_exchange_holidays() -> None:
    holidays = nyse_holidays(2024)

    assert {date(2024, 3, 29), date(2024, 7, 4), date(2024, 11, 28)} <= holidays
    assert date(2025, 4, 18) in nyse_holidays(2025)  # Good Friday from Easter
    assert date(2022, 12, 26) in nyse_holidays(2022)  # Christmas on a Sunday
    assert date(2023, 6, 19) in nyse_holidays(2023)  # Juneteenth, from 2022
    assert is_trading_day(date(2021, 6, 18))
    # New Year's Day on a Saturday is not observed on the Friday before.
    assert is_trading_day(date(2021, 12, 31))
    assert not is_trading_day(date(2024, 6, 29))


def test_regular_session_and_next_open_use_new_york_time() -> None:
    assert is_market_open(datetime(2024, 6, 28, 14, 0, tzinfo=UTC))  # 10:00 EDT
    assert not is_market_open(datetime(2024, 6, 28, 21, 0, tzinfo=UTC))  # 17:00 EDT
    assert not is_market_open(datetime(2024, 7, 4, 15, 0, tzinfo=UTC))
    friday_evening = datetime(2024, 6, 28, 22, 0, tzinfo=UTC)
    assert next_open(friday_evening) == datetime(2024, 7, 1, 13, 30, tzinfo=UTC)
    winter_morning = datetime(2024, 1, 2, 12, 0, tzinfo=UTC)  # 07:00 EST
    assert next_open(winter_morning) == datetime(2024, 1, 2, 14, 30, tzinfo=UTC)
