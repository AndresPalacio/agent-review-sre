from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from tests.domain.quant.synthetic import COMPANIES, company_metrics, line_items, metrics

from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.market import CompanyNews, FinancialMetrics, InsiderTrade, LineItem, Price
from fin_graph.domain.quant.common import Signal
from fin_graph.domain.quant.personas.ackman import score_ackman
from fin_graph.domain.quant.personas.burry import score_burry
from fin_graph.domain.quant.personas.damodaran import score_damodaran
from fin_graph.domain.quant.personas.munger import score_munger
from fin_graph.domain.quant.personas.pabrai import score_pabrai

FIXTURES = Path(__file__).parents[3] / "compat" / "fixtures" / "v1" / "personas" / "value-quality"


def _snapshot(name: str) -> CompanySnapshot:
    ticker, _, market_cap = COMPANIES[name]
    return CompanySnapshot(
        ticker=ticker,
        as_of=date(2026, 1, 1),
        metrics=tuple(company_metrics(name)),
        line_items=tuple(line_items(name)),
        market_cap=market_cap,
    )


def test_deep_value_components_match_recorded_reference_fixture() -> None:
    expected = json.loads((FIXTURES / "deep-value.json").read_text(encoding="utf-8"))
    snapshot = _snapshot("deep_value")
    scores = {
        "damodaran": score_damodaran(snapshot),
        "ackman": score_ackman(snapshot),
        "munger": score_munger(snapshot),
        "burry": score_burry(snapshot),
        "pabrai": score_pabrai(snapshot),
    }

    for name, score in scores.items():
        assert score.signal.value == expected[name]["signal"]
        assert score.score == pytest.approx(expected[name]["score"])
        assert {
            component.name: component.score
            for component in score.components
            if component.score is not None
        } == pytest.approx(expected[name]["components"])
    damodaran_value = scores["damodaran"].component("intrinsic_value")
    assert damodaran_value is not None
    assert damodaran_value.metrics["intrinsic_value"] is expected["damodaran"]["intrinsic_value"]
    assert scores["munger"].confidence == expected["munger"]["confidence"]


def test_quality_and_negative_value_cases_remain_finite_and_deterministic() -> None:
    quality = _snapshot("quality")
    negative = _snapshot("distressed")

    quality_scores = [
        score_damodaran(quality),
        score_ackman(quality),
        score_munger(quality),
        score_burry(quality),
        score_pabrai(quality),
    ]
    negative_scores = [
        score_damodaran(negative),
        score_ackman(negative),
        score_munger(negative),
        score_burry(negative),
        score_pabrai(negative),
    ]

    assert all(score.confidence >= 0 and score.confidence <= 100 for score in quality_scores)
    assert all(score.signal in Signal for score in negative_scores)
    assert all(score.ticker == "DIST" for score in negative_scores)
    assert score_pabrai(negative).signal is Signal.BEARISH


@pytest.mark.parametrize("ticker", ["AAPL", "MSFT", "TSLA"])
def test_metrics_without_statements_create_explicit_gaps(ticker: str) -> None:
    recorded_metrics = tuple(metrics(ticker))
    snapshot = CompanySnapshot(
        ticker=ticker,
        as_of=date(2026, 1, 1),
        metrics=recorded_metrics,
        market_cap=recorded_metrics[0].market_cap,
    )

    for scorer in (score_damodaran, score_ackman, score_munger, score_burry, score_pabrai):
        score = scorer(snapshot)
        assert score.data_gaps
        assert score.score is not None


def test_empty_snapshot_is_neutral_zero_confidence_with_data_gaps() -> None:
    snapshot = CompanySnapshot(ticker="EMPTY", as_of=date(2026, 1, 1))

    for scorer in (score_damodaran, score_ackman, score_munger, score_burry, score_pabrai):
        score = scorer(snapshot)
        assert score.signal is Signal.NEUTRAL
        assert score.confidence == 0
        assert score.data_gaps


@pytest.mark.parametrize(
    ("field", "observation"),
    [
        (
            "prices",
            Price(
                time=datetime(2026, 1, 2, tzinfo=UTC),
                open=1,
                high=1,
                low=1,
                close=1,
                volume=1,
            ),
        ),
        (
            "metrics",
            FinancialMetrics(ticker="FUT", report_period=date(2026, 1, 2)),
        ),
        (
            "line_items",
            LineItem(ticker="FUT", report_period=date(2026, 1, 2)),
        ),
        (
            "news",
            CompanyNews(
                ticker="FUT",
                title="Future headline",
                date=datetime(2026, 1, 2, tzinfo=UTC),
            ),
        ),
        (
            "insider_trades",
            InsiderTrade(ticker="FUT", filing_date=date(2026, 1, 2)),
        ),
    ],
)
def test_company_snapshot_rejects_future_observations(field: str, observation: object) -> None:
    with pytest.raises(ValueError, match=f"future {field}"):
        CompanySnapshot(
            ticker="FUT",
            as_of=date(2026, 1, 1),
            **{field: (observation,)},
        )


def test_company_snapshot_canonicalizes_sequences_for_latest_first_scorers() -> None:
    older, newer = date(2025, 1, 1), date(2025, 2, 1)
    old_price = Price(
        time=datetime(2025, 1, 1, tzinfo=UTC), open=1, high=1, low=1, close=1, volume=1
    )
    new_price = Price(
        time=datetime(2025, 2, 1, tzinfo=UTC), open=2, high=2, low=2, close=2, volume=2
    )
    snapshot = CompanySnapshot(
        ticker="ORD",
        as_of=newer,
        prices=(new_price, old_price),
        metrics=(
            FinancialMetrics(ticker="ORD", report_period=older),
            FinancialMetrics(ticker="ORD", report_period=newer),
        ),
        line_items=(
            LineItem(ticker="ORD", report_period=older),
            LineItem(ticker="ORD", report_period=newer),
        ),
        news=(
            CompanyNews(ticker="ORD", title="old", date=datetime(2025, 1, 1, tzinfo=UTC)),
            CompanyNews(ticker="ORD", title="new", date=datetime(2025, 2, 1, tzinfo=UTC)),
        ),
        insider_trades=(
            InsiderTrade(ticker="ORD", filing_date=older),
            InsiderTrade(ticker="ORD", filing_date=newer),
        ),
    )

    assert [item.day for item in snapshot.prices] == [older, newer]
    assert [item.report_period for item in snapshot.metrics] == [newer, older]
    assert [item.report_period for item in snapshot.line_items] == [newer, older]
    assert [item.title for item in snapshot.news] == ["new", "old"]
    assert [item.filing_date for item in snapshot.insider_trades] == [newer, older]
