from __future__ import annotations

import math

import pytest
from tests.domain.quant.synthetic import bars, reference

from fin_graph.domain.orders import OrderAction, compute_allowed_actions
from fin_graph.domain.portfolio import Portfolio, Position
from fin_graph.domain.quant import risk

SERIES = {
    "AAA": {"n": 120, "seed": 3},
    "BBB": {"n": 120, "seed": 11, "vol": 0.03},
    "CCC": {"n": 120, "seed": 3, "vol": 0.009},
}
PORTFOLIO = Portfolio(
    cash=100_000.0,
    positions={
        "AAA": Position(long=50, long_cost_basis=100.0),
        "CCC": Position(short=20, short_cost_basis=90.0),
    },
)


@pytest.fixture
def limits() -> dict[str, risk.PositionLimit]:
    prices = {ticker: bars(**kwargs) for ticker, kwargs in SERIES.items()}
    return risk.position_limits(list(SERIES), prices, PORTFOLIO)


@pytest.mark.parametrize("ticker", sorted(SERIES))
def test_position_limits_match_the_original(
    ticker: str, limits: dict[str, risk.PositionLimit]
) -> None:
    expected = reference()["risk"][ticker]
    limit = limits[ticker]

    assert limit.current_price == expected["price"]
    assert limit.remaining_limit == pytest.approx(expected["remaining"], rel=1e-12)
    assert limit.volatility.annualized == pytest.approx(expected["annualized_volatility"])
    assert limit.volatility.percentile == pytest.approx(expected["volatility_percentile"])
    assert limit.correlation.average == pytest.approx(expected["avg_correlation"])
    assert limit.correlation_multiplier == expected["correlation_multiplier"]
    assert limit.portfolio_value == pytest.approx(expected["portfolio_value"])
    assert limit.max_shares == reference()["max_shares"][ticker]


def test_allowed_actions_match_the_original(limits: dict[str, risk.PositionLimit]) -> None:
    prices = {ticker: limit.current_price for ticker, limit in limits.items()}
    shares = {ticker: limit.max_shares for ticker, limit in limits.items()}

    allowed = compute_allowed_actions(list(SERIES), prices, shares, PORTFOLIO)

    assert {
        ticker: {action.value: quantity for action, quantity in actions.items()}
        for ticker, actions in allowed.items()
    } == reference()["allowed"]


def test_volatility_buckets() -> None:
    assert risk.volatility_limit_pct(0.10) == pytest.approx(0.25)
    assert risk.volatility_limit_pct(0.20) == pytest.approx(0.20 * 0.975)
    assert risk.volatility_limit_pct(0.40) == pytest.approx(0.20 * 0.70)
    assert risk.volatility_limit_pct(0.80) == pytest.approx(0.10)
    assert [risk.correlation_multiplier(value) for value in (0.9, 0.7, 0.5, 0.3, 0.0)] == [
        0.70,
        0.85,
        1.00,
        1.05,
        1.10,
    ]


def test_a_ticker_without_prices_gets_no_room_and_the_riskiest_volatility() -> None:
    limits = risk.position_limits(["ZZZ"], {}, Portfolio())

    assert limits["ZZZ"].remaining_limit == 0 and limits["ZZZ"].max_shares == 0
    assert limits["ZZZ"].error
    assert limits["ZZZ"].volatility.annualized == pytest.approx(0.05 * math.sqrt(252))


def test_correlation_needs_five_common_days() -> None:
    few = {"A": bars(n=4, seed=1), "B": bars(n=4, seed=2)}
    many = {"A": bars(n=30, seed=1), "B": bars(n=30, seed=1)}

    assert risk.correlation_matrix(few) == {}
    assert risk.correlation_matrix(many)["A"]["B"] == pytest.approx(1.0)


def test_shorting_needs_a_risk_limit_and_margin() -> None:
    allowed = compute_allowed_actions(["AAA"], {"AAA": 100.0}, {"AAA": 0}, Portfolio(cash=10_000.0))

    assert allowed["AAA"] == {OrderAction.HOLD: 0}
