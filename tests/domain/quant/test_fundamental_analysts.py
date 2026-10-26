from __future__ import annotations

import math
from datetime import date

import pytest
from tests.domain.quant.synthetic import (
    COMPANIES,
    company_metrics,
    insider_trades,
    line_items,
    metrics,
    news,
    reference,
)

from fin_graph.domain.market import FinancialMetrics, LineItem
from fin_graph.domain.quant import buffett, fundamentals, graham, sentiment, valuation
from fin_graph.domain.quant.common import Signal

TICKERS = ["AAPL", "MSFT", "TSLA"]


@pytest.mark.parametrize("ticker", TICKERS)
def test_fundamentals_match_the_original(ticker: str) -> None:
    expected = reference()["fundamentals"][ticker]

    score = fundamentals.score_fundamentals(ticker, metrics(ticker))

    assert score.signal.value == expected["signal"]
    assert score.confidence == expected["confidence"]
    assert {item.name: item.signal.value for item in score.components} == {
        key.removesuffix("_signal"): value for key, value in expected["parts"].items()
    }


def test_fundamentals_without_metrics_is_a_zero_confidence_gap() -> None:
    score = fundamentals.score_fundamentals("AAPL", [])

    assert score.degraded and score.data_gaps == ["no financial metrics"]


def test_missing_metrics_are_gaps_not_zeros() -> None:
    bare = FinancialMetrics(ticker="X", report_period=date(2024, 1, 1), return_on_equity=0.3)

    score = fundamentals.score_fundamentals("X", [bare])

    assert "net_margin missing" in score.data_gaps
    assert score.component("profitability").score == 1  # type: ignore[union-attr]


@pytest.mark.parametrize("ticker", TICKERS)
def test_sentiment_matches_the_original(ticker: str) -> None:
    expected = reference()["sentiment"][ticker]

    score = sentiment.score_sentiment(
        ticker, insider_trades(ticker), news(ticker, rotate_labels=True)
    )

    assert score.signal.value == expected["signal"]
    assert math.isclose(score.confidence, expected["confidence"])
    insiders = score.component("insider_trading")
    assert insiders is not None
    assert insiders.metrics["bullish_trades"] == expected["bullish_trades"]
    assert insiders.metrics["bearish_trades"] == expected["bearish_trades"]


@pytest.mark.parametrize(
    ("headline", "label"),
    [
        ("Apple beats estimates and raises guidance", "positive"),
        ("Tesla shares plunge after recall and SEC probe", "negative"),
        ("Apple (AAPL) Ascends While Market Falls: Some Facts to Note", "neutral"),
        ("Microsoft to hold annual shareholder meeting", "neutral"),
        ("Analyst downgrades Nvidia, cuts target", "negative"),
    ],
)
def test_headline_lexicon(headline: str, label: str) -> None:
    assert sentiment.headline_sentiment(headline) == label


def test_unlabelled_headlines_fall_back_to_the_lexicon_and_labels_override() -> None:
    items = news("AAPL")[:3]

    plain = sentiment.news_tone(items)
    forced = sentiment.news_tone(items, {item.url: "negative" for item in items})

    assert plain.metrics["labelled_by_lexicon"] == 3
    assert forced.signal is Signal.BEARISH and forced.metrics["labelled_by_lexicon"] == 0


@pytest.mark.parametrize("name", sorted(COMPANIES))
def test_valuation_matches_the_original(name: str) -> None:
    ticker, _, market_cap = COMPANIES[name]
    expected = reference()["valuation"][name]

    score = valuation.score_valuation(
        ticker, company_metrics(name)[:8], line_items(name)[:8], market_cap
    )

    if expected is None:
        assert score.degraded
        return
    assert score.signal.value == expected["signal"]
    assert score.confidence == expected["confidence"]
    for method, values in expected["methods"].items():
        component = score.component(method)
        assert component is not None, method
        assert round(component.metrics["value"], 2) == pytest.approx(values["value"], rel=1e-12)
        assert component.signal.value == values["signal"]


def test_capex_is_an_outflow_whatever_its_sign() -> None:
    """The original added back negative capex; providers report it negative."""
    items = line_items("deep_value")
    negative = [
        item.model_copy(update={"capital_expenditure": -(item.value("capital_expenditure") or 0)})
        for item in items
    ]
    args = ("DEEP", company_metrics("deep_value")[:8])

    positive_score = valuation.score_valuation(*args, items[:8], 0.6e9)
    negative_score = valuation.score_valuation(*args, negative[:8], 0.6e9)

    assert positive_score.component("owner_earnings") == negative_score.component("owner_earnings")


def test_valuation_needs_two_periods_and_a_market_cap() -> None:
    items = line_items("quality")

    assert valuation.score_valuation("AAPL", metrics("AAPL"), items[:1], 1e12).degraded
    assert valuation.score_valuation("AAPL", metrics("AAPL"), items, None).degraded


@pytest.mark.parametrize("name", sorted(COMPANIES))
def test_graham_matches_the_original(name: str) -> None:
    ticker, _, market_cap = COMPANIES[name]
    expected = reference()["graham"][name]

    score = graham.score_graham(ticker, company_metrics(name), line_items(name), market_cap)

    assert score.score == expected["total"]
    assert [item.score for item in score.components] == [
        expected["earnings"],
        expected["strength"],
        expected["valuation"],
    ]
    assert 0 <= score.confidence <= 100


def test_graham_signal_bands() -> None:
    deep = graham.score_graham(
        "DEEP", company_metrics("deep_value"), line_items("deep_value"), 0.6e9
    )
    dist = graham.score_graham(
        "DIST", company_metrics("distressed"), line_items("distressed"), 0.25e9
    )

    assert deep.signal is Signal.BULLISH
    assert dist.signal is Signal.BEARISH


@pytest.mark.parametrize("name", sorted(COMPANIES))
def test_buffett_matches_the_original(name: str) -> None:
    ticker, _, market_cap = COMPANIES[name]
    expected = reference()["buffett"][name]
    items = line_items(name)

    score = buffett.score_buffett(ticker, company_metrics(name), items, market_cap)

    for part in (
        "fundamentals",
        "consistency",
        "moat",
        "pricing_power",
        "book_value",
        "management",
    ):
        component = score.component(part)
        assert component is not None and component.score == expected[part], part
    value = score.component("intrinsic_value")
    assert value is not None
    assert value.metrics["intrinsic_value"] == pytest.approx(expected["intrinsic_value"])
    assert value.metrics["owner_earnings"] == pytest.approx(expected["owner_earnings"])
    assert buffett.maintenance_capex(items) == pytest.approx(expected["maintenance_capex"])


def test_buffett_needs_a_margin_of_safety_to_be_bullish() -> None:
    expensive = buffett.score_buffett(
        "AAPL", company_metrics("quality"), line_items("quality"), 2.9e12
    )

    margin = expensive.component("intrinsic_value").metrics["margin_of_safety"]  # type: ignore[union-attr]
    assert margin < buffett.EXPENSIVE_MARGIN
    assert expensive.signal is Signal.BEARISH


def test_buffett_pricing_power_uses_gross_profit_when_no_margin_is_given() -> None:
    items = [
        LineItem(
            ticker="X",
            report_period=date(2023 - index, 12, 31),
            gross_profit=60.0 - 3 * index,
            revenue=100.0,
        )
        for index in range(4)
    ]

    component = buffett.pricing_power(items, metrics("AAPL"))

    # Margins 60% → 51%: newest two average 6 points above the oldest two, mean above 50%.
    assert component.score == 3 + 2
