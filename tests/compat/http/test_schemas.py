from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from fin_graph.adapters.reference_app.mapping import (
    ReferenceAdapterError,
    from_native_backtest_result,
    from_native_signal,
    to_native_analysis_request,
    to_native_backtest_request,
)
from fin_graph.adapters.reference_app.schemas import (
    ActionResponse,
    AlgorithmBacktestConfig,
    ApiKeyCreateRequest,
    BacktestRequest,
    BaseEvent,
    FlowResponse,
    FlowRunStatus,
    HedgeFundRequest,
    ModelProvider,
    OllamaStatusResponse,
    PortfolioPosition,
    ProgressResponse,
    SaveJsonRequest,
    SignalResponse,
    StartEvent,
    TrainRequest,
    VaRResponse,
)
from fin_graph.backtesting.models import (
    BacktestMetrics as NativeBacktestMetrics,
)
from fin_graph.backtesting.models import (
    BacktestRequest as NativeBacktestRequest,
)
from fin_graph.backtesting.models import (
    BacktestResult as NativeBacktestResult,
)
from fin_graph.backtesting.models import (
    BacktestSnapshot as NativeBacktestSnapshot,
)
from fin_graph.backtesting.models import (
    BacktestStatus,
)
from fin_graph.domain.orders import OrderAction
from fin_graph.domain.portfolio import Portfolio, Position, RealizedGains
from fin_graph.domain.quant.common import Signal
from fin_graph.quant.signals.base import Signal as NativeSignal

FIXTURE = Path(__file__).parents[1] / "fixtures" / "http" / "schema_examples.json"
EXAMPLES = json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_hedge_fund_request_matches_the_recorded_shape_and_adapts_losslessly() -> None:
    request = HedgeFundRequest.model_validate(EXAMPLES["hedge_fund_request"])

    assert request.model_dump(mode="json") == EXAMPLES["hedge_fund_request"]
    native = to_native_analysis_request(request)

    assert native.tickers == ["AAPL", "MSFT"]
    assert native.as_of == date(2025, 1, 31)
    assert [item.value for item in native.analysts or ()] == [
        "ben_graham",
        "technical_analyst",
    ]
    assert native.portfolio is not None
    assert native.portfolio.cash == 100_000.125
    assert native.portfolio.position("AAPL") == Position(long=10, long_cost_basis=100.25)
    assert native.portfolio.position("MSFT") == Position(
        short=5,
        short_cost_basis=400,
        short_margin_used=1_000,
    )
    assert native.portfolio.margin_used == 1_000


def test_request_defaults_and_reference_extra_field_policy_are_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEFAULT_MODEL_NAME", raising=False)
    monkeypatch.delenv("DEFAULT_MODEL_PROVIDER", raising=False)
    request = HedgeFundRequest.model_validate(
        {
            "tickers": ["AAPL"],
            "graph_nodes": [],
            "graph_edges": [],
            "unknown_reference_field": "ignored",
        }
    )

    assert request.model_name == "gpt-4.1"
    assert request.model_provider is ModelProvider.OPENAI
    assert request.margin_requirement == 0.0
    assert request.initial_cash == 100_000.0
    assert request.model_dump(mode="json")["start_date"] is None


def test_recorded_validation_location_and_message_for_bad_trade_price() -> None:
    with pytest.raises(ValidationError) as error:
        PortfolioPosition(ticker="AAPL", quantity=1, trade_price=0)

    issue = error.value.errors()[0]
    assert issue["loc"] == ("trade_price",)
    assert "Trade price must be positive!" in issue["msg"]


def test_backtest_request_adapter_rejects_bad_dates_and_fractional_shares() -> None:
    payload = {
        "tickers": ["AAPL"],
        "graph_nodes": [{"id": "ben_graham"}],
        "graph_edges": [],
        "start_date": "2025-02-01",
        "end_date": "2025-01-01",
        "portfolio_positions": [],
    }
    with pytest.raises(ReferenceAdapterError, match="start_date"):
        to_native_backtest_request(BacktestRequest.model_validate(payload))

    payload.update(
        start_date="2025-01-01",
        end_date="2025-02-01",
        portfolio_positions=[{"ticker": "AAPL", "quantity": 1.5, "trade_price": 100}],
    )
    with pytest.raises(ReferenceAdapterError, match="whole shares"):
        to_native_backtest_request(BacktestRequest.model_validate(payload))


def test_native_backtest_result_serializes_to_the_exact_legacy_keys() -> None:
    request = NativeBacktestRequest(
        tickers=["AAPL"],
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 2),
        initial_capital=10_000,
        analysts=["ben_graham"],
    )
    portfolio = Portfolio(
        cash=9_100.25,
        positions={"AAPL": Position(long=10, long_cost_basis=90)},
        realized_gains={"AAPL": RealizedGains(long=0.125)},
    )
    snapshot = NativeBacktestSnapshot(
        date=date(2025, 1, 2),
        portfolio=portfolio,
        portfolio_value=10_100.25,
        decisions={
            "AAPL": {
                "action": OrderAction.HOLD,
                "quantity": 0,
                "confidence": 70,
                "reasoning": "fixture",
            }
        },
        executed_trades={"AAPL": 0},
        realized_pnl={"AAPL": 0.125},
        analyst_signals={
            "ben_graham_agent": {
                "AAPL": {"signal": Signal.BULLISH, "confidence": 70, "reasoning": "fixture"}
            }
        },
        current_prices={"AAPL": 100},
        long_exposure=1_000,
        gross_exposure=1_000,
        net_exposure=1_000,
    )
    result = NativeBacktestResult(
        request=request,
        snapshots=(snapshot,),
        metrics=NativeBacktestMetrics(
            sharpe_ratio=1.25,
            max_drawdown_pct=-2.5,
            max_drawdown_date=date(2025, 1, 2),
            gross_exposure=1_000,
            net_exposure=1_000,
        ),
        final_portfolio=portfolio,
        status=BacktestStatus.COMPLETED,
    )

    legacy = from_native_backtest_result(result).model_dump(mode="json")

    assert set(legacy) == {"results", "performance_metrics", "final_portfolio"}
    assert legacy["results"][0]["date"] == "2025-01-02"
    assert legacy["results"][0]["cash"] == 9_100.25
    assert legacy["performance_metrics"]["max_drawdown"] == -2.5
    assert legacy["final_portfolio"]["realized_gains"]["AAPL"]["long"] == 0.125


def test_sse_and_datetime_json_match_recorded_wire_format() -> None:
    event: BaseEvent = StartEvent(timestamp="2025-01-01T12:00:00Z")
    flow = FlowResponse.model_validate(EXAMPLES["flow_response"])

    assert event.to_sse() == EXAMPLES["start_event_sse"]
    assert flow.model_dump(mode="json") == EXAMPLES["flow_response"]
    assert FlowRunStatus.IN_PROGRESS.value == "IN_PROGRESS"


def test_v2_and_settings_route_schemas_keep_their_public_fields() -> None:
    assert {provider.value for provider in ModelProvider} == {
        "Alibaba",
        "Anthropic",
        "DeepSeek",
        "Google",
        "Groq",
        "Kimi",
        "Meta",
        "Mistral",
        "OpenAI",
        "Ollama",
        "OpenRouter",
        "GigaChat",
        "Azure OpenAI",
        "xAI",
    }
    assert ApiKeyCreateRequest(provider="OpenAI", key_value="secret").is_active
    assert (
        OllamaStatusResponse(
            installed=True,
            running=False,
            available_models=[],
            server_url="http://localhost:11434",
        ).error
        is None
    )
    assert ActionResponse(success=True, message="ok").success
    assert ProgressResponse(status="pulling", percentage=50).percentage == 50
    assert SaveJsonRequest(filename="result.json", data={"ok": True}).data == {"ok": True}
    assert TrainRequest(tickers=["AAPL"], date="2025-01-01").tickers == ["AAPL"]
    assert (
        VaRResponse(
            var=10,
            cvar=12,
            confidence=0.95,
            horizon_days=1,
            n_sims=1_000,
            portfolio_value=1_000,
            expected_pnl=1,
            portfolio_sigma=0.1,
            var_pct=0.01,
            holdings={"AAPL": 10},
            date="2025-01-01",
        ).n_sims
        == 1_000
    )
    config = AlgorithmBacktestConfig(
        algorithm_key="momentum",
        symbols=["aapl", "AAPL"],
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 31),
    )
    assert config.symbols == ["AAPL"]


def test_native_signal_adapter_preserves_metadata_and_date() -> None:
    native = NativeSignal(
        model_name="technical_ml",
        ticker="AAPL",
        as_of=date(2025, 1, 2),
        value=0.75,
        reasoning="fixture",
        metadata={"window": 20},
    )

    response = from_native_signal(native)

    assert response == SignalResponse(
        ticker="AAPL",
        date="2025-01-02",
        value=0.75,
        reasoning="fixture",
        metadata={"window": 20},
    )


def test_api_response_datetime_keeps_utc_offset() -> None:
    response = FlowResponse(
        id=1,
        name="x",
        description=None,
        nodes=[],
        edges=[],
        viewport=None,
        data=None,
        is_template=False,
        tags=None,
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        updated_at=None,
    )

    assert response.model_dump(mode="json")["created_at"] == "2025-01-01T00:00:00Z"
