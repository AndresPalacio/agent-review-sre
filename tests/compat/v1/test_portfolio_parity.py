from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from fin_graph.agents.analysis.persona_team import PersonaFinding
from fin_graph.compat.ai_hedge_fund.models import (
    LegacyHedgeFundRequest,
    LegacyPortfolio,
    LegacyRiskAnalysis,
)
from fin_graph.compat.ai_hedge_fund.portfolio import build_legacy_portfolio_result
from fin_graph.domain.analysts import AnalystId
from fin_graph.domain.fills import apply_fill
from fin_graph.domain.market import Price
from fin_graph.domain.orders import OrderAction
from fin_graph.domain.portfolio import Portfolio, Position
from fin_graph.domain.quant.common import QuantScore, Signal
from fin_graph.domain.tools import Domain

FIXTURE = Path(__file__).parents[1] / "fixtures" / "v1" / "portfolio" / "decisions.json"


def bar(day: date, close: float) -> Price:
    return Price(
        time=datetime(day.year, day.month, day.day, 21, tzinfo=UTC),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000,
    )


def finding(
    signal: Signal,
    confidence: float,
    *,
    ticker: str = "AAPL",
    analyst_id: AnalystId = AnalystId.BEN_GRAHAM,
) -> PersonaFinding:
    return PersonaFinding(
        analyst_id=analyst_id,
        ticker=ticker,
        score=QuantScore(
            analyst=Domain.GRAHAM,
            ticker=ticker,
            signal=signal,
            confidence=confidence,
        ),
        reasoning=f"{analyst_id.value} says {signal.value}",
    )


@pytest.mark.parametrize("case_name", json.loads(FIXTURE.read_text()).keys())
def test_long_short_hold_decisions_match_recorded_fixture(case_name: str) -> None:
    case: dict[str, Any] = json.loads(FIXTURE.read_text())[case_name]
    portfolio = Portfolio.model_validate(case["portfolio"])
    prices = case["prices"]
    result = build_legacy_portfolio_result(
        [finding(Signal(case["signal"]), case["confidence"])], portfolio, prices
    )

    decision = result.decisions["AAPL"]
    assert {
        "action": decision.action.value,
        "quantity": decision.quantity,
        "confidence": decision.confidence,
    } == case["expected"]
    if decision.action is not OrderAction.HOLD:
        fill = apply_fill(
            portfolio,
            "AAPL",
            decision.action,
            decision.quantity,
            prices["AAPL"],
        )
        assert fill.executed == decision.quantity


def test_legacy_wire_models_keep_reference_field_names() -> None:
    request = LegacyHedgeFundRequest(
        tickers=["aapl"],
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 31),
        portfolio=LegacyPortfolio(cash=10_000),
        selected_analysts=[AnalystId.BEN_GRAHAM],
        model_name="gpt-4.1",
        model_provider="OpenAI",
    )
    result = build_legacy_portfolio_result(
        [finding(Signal.BULLISH, 80)], request.portfolio.to_native(), {"AAPL": 100.0}
    )

    assert request.tickers == ("AAPL",)
    assert set(request.model_dump(mode="json")) == {
        "tickers",
        "start_date",
        "end_date",
        "portfolio",
        "show_reasoning",
        "selected_analysts",
        "model_name",
        "model_provider",
    }
    assert set(result.model_dump(mode="json")) == {"decisions", "analyst_signals"}
    analyst_signal = result.analyst_signals["ben_graham_agent"]["AAPL"]
    assert not isinstance(analyst_signal, LegacyRiskAnalysis)
    assert analyst_signal.signal is Signal.BULLISH
    assert isinstance(result.analyst_signals["risk_management_agent"]["AAPL"], LegacyRiskAnalysis)


def test_legacy_portfolio_round_trips_short_margin_and_realized_gains() -> None:
    payload = {
        "cash": 8_000.0,
        "margin_used": 1_500.0,
        "margin_requirement": 0.5,
        "positions": {
            "AAPL": {
                "long": 2,
                "short": 10,
                "long_cost_basis": 90.0,
                "short_cost_basis": 110.0,
                "short_margin_used": 1_500.0,
            }
        },
        "realized_gains": {"AAPL": {"long": 12.5, "short": -7.0}},
    }

    portfolio = LegacyPortfolio.model_validate(payload)
    native = portfolio.to_native()

    assert portfolio.model_dump(mode="json") == payload
    assert native.margin_used == 1_500.0
    assert native.position("AAPL").short_margin_used == 1_500.0
    assert native.realized_gains["AAPL"].long == 12.5
    assert native.realized_gains["AAPL"].short == -7.0
    assert LegacyPortfolio.from_native(native).model_dump(mode="json") == payload


def test_real_price_histories_drive_volatility_limits_and_risk_payload() -> None:
    first = date(2025, 1, 1)
    flat_history = tuple(bar(first + timedelta(days=offset), 100.0) for offset in range(70))
    result = build_legacy_portfolio_result(
        [finding(Signal.BULLISH, 90)],
        Portfolio(cash=10_000),
        {"AAPL": 100.0},
        price_histories={"AAPL": flat_history},
    )

    risk = result.analyst_signals["risk_management_agent"]["AAPL"]
    assert isinstance(risk, LegacyRiskAnalysis)
    assert risk.volatility_metrics is not None
    assert risk.volatility_metrics.data_points == 60
    assert risk.remaining_position_limit == pytest.approx(2_500.0)
    assert result.decisions["AAPL"].quantity == 25


def test_failed_findings_are_not_published_as_neutral_votes() -> None:
    failed = PersonaFinding(
        analyst_id=AnalystId.BEN_GRAHAM,
        ticker="AAPL",
        error={
            "code": "data_unavailable",
            "message": "Company data is temporarily unavailable.",
            "correlation_id": "0123456789abcdef",
        },
    )

    result = build_legacy_portfolio_result([failed], Portfolio(cash=10_000), {"AAPL": 100.0})

    assert "ben_graham_agent" not in result.analyst_signals
    assert result.decisions["AAPL"].reasoning == "No analyst signals"


def test_contradictory_votes_and_no_votes_hold_deterministically() -> None:
    contradictory = build_legacy_portfolio_result(
        [
            finding(Signal.BULLISH, 80, analyst_id=AnalystId.BEN_GRAHAM),
            finding(Signal.BEARISH, 80, analyst_id=AnalystId.WARREN_BUFFETT),
        ],
        Portfolio(cash=10_000),
        {"AAPL": 100.0},
    )
    empty = build_legacy_portfolio_result([], Portfolio(cash=10_000), {"AAPL": 100.0})

    assert contradictory.decisions["AAPL"].action is OrderAction.HOLD
    assert contradictory.decisions["AAPL"].confidence == 0
    assert empty.decisions["AAPL"].action is OrderAction.HOLD
    assert empty.decisions["AAPL"].reasoning == "No analyst signals"


@pytest.mark.parametrize("price", [0.0, -1.0, float("nan"), float("inf")])
def test_non_positive_prices_never_produce_an_order(price: float) -> None:
    result = build_legacy_portfolio_result(
        [finding(Signal.BEARISH, 90)],
        Portfolio(cash=1_000, positions={"AAPL": Position(long=3, long_cost_basis=100)}),
        {"AAPL": price},
    )

    assert result.decisions["AAPL"].action is OrderAction.HOLD
    assert result.decisions["AAPL"].quantity == 0


def test_cash_shares_margin_and_mixed_positions_stay_inside_hard_limits() -> None:
    no_cash = build_legacy_portfolio_result(
        [finding(Signal.BULLISH, 90)], Portfolio(cash=0), {"AAPL": 100.0}
    )
    no_margin = build_legacy_portfolio_result(
        [finding(Signal.BEARISH, 90)], Portfolio(cash=0), {"AAPL": 100.0}
    )
    unaffordable_cover = build_legacy_portfolio_result(
        [finding(Signal.BULLISH, 90)],
        Portfolio(
            cash=0,
            positions={"AAPL": Position(short=1, short_cost_basis=100)},
            margin_used=50,
        ),
        {"AAPL": 200.0},
    )
    mixed = Portfolio(
        cash=10_000,
        positions={"AAPL": Position(long=3, short=2, long_cost_basis=90, short_cost_basis=110)},
        margin_used=110,
    )
    sell = build_legacy_portfolio_result([finding(Signal.BEARISH, 60)], mixed, {"AAPL": 100.0})
    cover = build_legacy_portfolio_result([finding(Signal.BULLISH, 60)], mixed, {"AAPL": 100.0})

    assert no_cash.decisions["AAPL"].action is OrderAction.HOLD
    assert no_margin.decisions["AAPL"].action is OrderAction.HOLD
    assert unaffordable_cover.decisions["AAPL"].action is OrderAction.HOLD
    assert sell.decisions["AAPL"].quantity == 3
    assert cover.decisions["AAPL"].quantity == 2


def test_duplicate_analyst_ticker_findings_are_rejected() -> None:
    duplicate = finding(Signal.BULLISH, 80)

    with pytest.raises(ValueError, match="duplicate finding"):
        build_legacy_portfolio_result(
            [duplicate, duplicate], Portfolio(cash=10_000), {"AAPL": 100.0}
        )
