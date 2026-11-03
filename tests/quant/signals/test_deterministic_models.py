from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from fin_graph.agents.common.testing.scripted_model import ScriptedChatModel
from fin_graph.domain.market import CompanyNews, FinancialMetrics, Price
from fin_graph.quant.signals import alpha_model_registry
from fin_graph.quant.signals.base import Signal
from fin_graph.quant.signals.buffett import BuffettAlphaModel
from fin_graph.quant.signals.candlestick import PATTERN_NAMES, CandlestickPatternModel
from fin_graph.quant.signals.monte_carlo import MonteCarloAlphaModel
from fin_graph.quant.signals.news_sentiment import NewsSentimentModel
from fin_graph.quant.signals.pead import EarningsData, EarningsRecord, PEADModel

REFERENCE = json.loads(
    (
        Path(__file__).parents[2] / "compat/fixtures/v2/signals/deterministic/reference.json"
    ).read_text(encoding="utf-8")
)


def _reference_payload(signal: Signal, *, additive_metadata: tuple[str, ...] = ()):
    payload = signal.model_dump(mode="json")
    payload["date"] = payload.pop("as_of")
    for key in additive_metadata:
        payload["metadata"].pop(key, None)
    return payload


class Data:
    def __init__(self) -> None:
        self.price_rows: list[Price] = []
        self.metric_rows: list[FinancialMetrics] = []
        self.news_rows: list[CompanyNews] = []
        self.earnings_rows: list[EarningsRecord] = []

    async def prices(self, ticker: str, start: date, end: date):
        return [item for item in self.price_rows if start <= item.day <= end]

    async def financial_metrics(self, ticker: str, as_of: date, **kwargs):
        return [item for item in self.metric_rows if item.available_on <= as_of]

    async def line_items(self, ticker: str, names, as_of: date, **kwargs):
        return []

    async def insider_trades(self, ticker: str, as_of: date, **kwargs):
        return []

    async def company_news(self, ticker: str, as_of: date, *, start=None, limit=100):
        return [
            item
            for item in self.news_rows
            if item.date.date() <= as_of and (start is None or item.date.date() >= start)
        ][:limit]

    async def market_cap(self, ticker: str, as_of: date):
        return 1_000.0

    async def earnings_history(self, ticker: str, *, limit: int = 12):
        return self.earnings_rows[:limit]


def _bar(index: int, open_: float, high: float, low: float, close: float) -> Price:
    return Price(
        time=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=index),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1_000,
    )


def _metric(index: int) -> FinancialMetrics:
    day = date(2024, 12, 31) - timedelta(days=90 * index)
    return FinancialMetrics(
        ticker="CASE",
        report_period=day,
        filing_date=day,
        return_on_equity=0.25,
        gross_margin=0.5,
        operating_margin=0.25,
        net_margin=0.2,
        book_value_per_share=10 - index,
        debt_to_equity=0.2,
        current_ratio=2,
        market_cap=1_000,
    )


def test_signal_is_finite_and_clamped() -> None:
    assert Signal(model_name="x", ticker="A", as_of=date(2025, 1, 1), value=2).value == 1
    with pytest.raises(ValueError):
        Signal(model_name="x", ticker="A", as_of=date(2025, 1, 1), value=math.nan)


def test_registry_has_every_frozen_reference_key_without_eager_heavy_imports() -> None:
    assert tuple(alpha_model_registry()) == (
        "pead",
        "buffett",
        "candlestick",
        "technical_ml",
        "technical_scan",
        "news_sentiment",
        "arima",
        "lstm",
        "montecarlo",
    )


async def test_pead_is_point_in_time_deduplicated_and_fresh() -> None:
    data = Data()
    data.earnings_rows = [
        EarningsRecord(
            ticker="CASE",
            report_period=date(2025, 6, 30),
            filing_date=date(2025, 8, 1),
            source_type="10-Q",
            quarterly=EarningsData(eps_surprise="MISS"),
        ),
        EarningsRecord(
            ticker="CASE",
            report_period=date(2025, 6, 30),
            filing_date=date(2025, 8, 1),
            source_type="8-K",
            quarterly=EarningsData(eps_surprise="BEAT"),
        ),
    ]

    signal = await PEADModel().predict("CASE", date(2025, 8, 2), data)
    before = await PEADModel().predict("CASE", date(2025, 7, 31), data)

    assert signal.value == 1
    assert signal.metadata["source_type"] == "8-K"
    assert before.value == 0
    assert _reference_payload(signal) == REFERENCE["pead"]


async def test_candlestick_ports_all_patterns_and_uses_adjusted_ohlc() -> None:
    assert len(PATTERN_NAMES) == 18
    data = Data()
    data.price_rows = [
        *[_bar(i, 10 - i * 0.1, 10.1 - i * 0.1, 9.8 - i * 0.1, 9.9 - i * 0.1) for i in range(8)],
        _bar(8, 9.5, 9.6, 8.9, 9.0),
        _bar(9, 8.9, 9.8, 8.8, 9.7),
    ]
    # Uniform adjusted scaling must preserve the engulfing geometry.
    data.price_rows[-2] = data.price_rows[-2].model_copy(update={"adj_close": 4.5})
    data.price_rows[-1] = data.price_rows[-1].model_copy(update={"adj_close": 4.85})

    signal = await CandlestickPatternModel().predict("CASE", date(2025, 1, 10), data)

    assert signal.value > 0
    assert "Bullish Engulfing" in signal.metadata["patterns"]
    assert _reference_payload(signal) == REFERENCE["candlestick"]


async def test_news_sentiment_uses_only_recent_headlines() -> None:
    data = Data()
    data.news_rows = [
        CompanyNews(
            ticker="CASE",
            title="Company crushes expectations with outstanding record profit",
            date=datetime(2025, 1, 10, tzinfo=UTC),
        ),
        CompanyNews(
            ticker="CASE",
            title="Old fraud collapse",
            date=datetime(2024, 1, 1, tzinfo=UTC),
        ),
    ]

    signal = await NewsSentimentModel(lookback_days=7).predict("CASE", date(2025, 1, 11), data)

    assert signal.value > 0
    assert signal.metadata["n_articles"] == 1
    assert _reference_payload(signal) == REFERENCE["news_sentiment"]


async def test_buffett_structured_output_and_failure_abstention() -> None:
    data = Data()
    data.metric_rows = [_metric(index) for index in range(4)]
    valid = '{"signal":"bullish","confidence":80,"reasoning":"Durable economics."}'
    model = ScriptedChatModel(responses=[AIMessage(valid), AIMessage(valid)])
    signal = await BuffettAlphaModel(model).predict("CASE", date(2025, 1, 15), data)

    broken = ScriptedChatModel(responses=[AIMessage("bad"), AIMessage("still bad")])
    abstained = await BuffettAlphaModel(broken).predict("CASE", date(2025, 1, 15), data)

    assert signal.value == 0.8
    assert signal.metadata["abstained"] is False
    assert abstained.value == 0
    assert abstained.metadata["abstained"] is True


async def test_monte_carlo_is_seeded_bounded_and_adjusted() -> None:
    data = Data()
    base = date(2024, 1, 1)
    data.price_rows = [
        Price(
            time=datetime.combine(base + timedelta(days=index), datetime.min.time(), UTC),
            open=(close := 100 + index * 0.05 + 3 * math.sin(index / 10)) - 0.1,
            high=close + 0.3,
            low=close - 0.3,
            close=close,
            adj_close=close,
            volume=1_000_000,
        )
        for index in range(220)
    ]
    first = await MonteCarloAlphaModel(seed=7, n_sims=1000, min_history_days=100).predict(
        "CASE", data.price_rows[-1].day, data
    )
    second = await MonteCarloAlphaModel(seed=7, n_sims=1000, min_history_days=100).predict(
        "CASE", data.price_rows[-1].day, data
    )

    assert first == second
    assert -1 <= first.value <= 1
    assert first.metadata["last_close"] == data.price_rows[-1].adj_close
    assert first.metadata["seed"] == 7
    assert (
        _reference_payload(first, additive_metadata=("seed", "simulations"))
        == REFERENCE["montecarlo"]
    )
