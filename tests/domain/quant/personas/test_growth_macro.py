from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from fin_graph.domain.analysts import analyst_catalog
from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.market import CompanyNews, FinancialMetrics, InsiderTrade, LineItem, Price
from fin_graph.domain.quant.common import Signal
from fin_graph.domain.quant.personas.druckenmiller import score_druckenmiller
from fin_graph.domain.quant.personas.fisher import score_fisher
from fin_graph.domain.quant.personas.growth import score_growth
from fin_graph.domain.quant.personas.jhunjhunwala import score_jhunjhunwala
from fin_graph.domain.quant.personas.lynch import score_lynch
from fin_graph.domain.quant.personas.news import score_news
from fin_graph.domain.quant.personas.registry import registered_scorer_keys
from fin_graph.domain.quant.personas.taleb import score_taleb
from fin_graph.domain.quant.personas.wood import score_wood

AS_OF = date(2026, 1, 1)
FIXTURE = json.loads(
    (
        Path(__file__).parents[3]
        / "compat"
        / "fixtures"
        / "v1"
        / "personas"
        / "growth-macro"
        / "cases.json"
    ).read_text(encoding="utf-8")
)


def _metrics(**updates: float) -> tuple[FinancialMetrics, ...]:
    base = {
        "ticker": "CASE",
        "report_period": date(2025, 12, 31),
        "return_on_equity": 0.22,
        "return_on_invested_capital": 0.18,
        "revenue_growth": 0.25,
        "earnings_growth": 0.25,
        "earnings_per_share_growth": 0.25,
        "free_cash_flow_growth": 0.20,
        "operating_income_growth": 0.14,
        "gross_margin": 0.55,
        "operating_margin": 0.22,
        "debt_to_equity": 0.25,
        "current_ratio": 2.0,
        "peg_ratio": 0.8,
        "price_to_sales_ratio": 1.5,
        "market_cap": 1_000_000_000,
    }
    return (FinancialMetrics(**(base | updates)),)


def _items(**updates: float) -> tuple[LineItem, ...]:
    rows = []
    for index in range(6):
        factor = 1.12**-index
        values = {
            "revenue": 1_000_000_000 * factor,
            "net_income": 150_000_000 * factor,
            "operating_income": 220_000_000 * factor,
            "free_cash_flow": 170_000_000 * factor,
            "gross_margin": 0.45,
            "operating_margin": 0.22,
            "total_debt": 100_000_000,
            "cash_and_equivalents": 250_000_000,
            "shareholders_equity": 600_000_000,
            "total_assets": 1_000_000_000,
            "total_liabilities": 400_000_000,
            "current_assets": 500_000_000,
            "current_liabilities": 200_000_000,
            "capital_expenditure": -30_000_000,
            "research_and_development": 100_000_000,
            "outstanding_shares": 100_000_000,
        }
        if index == 0:
            values.update(updates)
        rows.append(
            LineItem(
                ticker="CASE",
                report_period=date(2025 - index, 12, 31),
                period="annual",
                **values,
            )
        )
    return tuple(rows)


def _prices(closes: list[float]) -> tuple[Price, ...]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    return tuple(
        Price(
            time=start + timedelta(days=index),
            open=value,
            high=value,
            low=value,
            close=value,
            volume=1_000_000,
        )
        for index, value in enumerate(closes)
    )


def _snapshot(**updates: object) -> CompanySnapshot:
    values = {
        "ticker": "CASE",
        "as_of": AS_OF,
        "metrics": _metrics(),
        "line_items": _items(),
        "prices": _prices([100 + index for index in range(80)]),
        "market_cap": 1_000_000_000,
    }
    values.update(updates)
    return CompanySnapshot(**values)


def test_growth_and_contraction_are_distinct_deterministic_cases() -> None:
    growth = _snapshot()
    contraction = _snapshot(
        metrics=_metrics(
            revenue_growth=-0.2,
            earnings_growth=-0.3,
            earnings_per_share_growth=-0.3,
            free_cash_flow_growth=-0.25,
            operating_income_growth=-0.2,
            gross_margin=0.2,
            operating_margin=0.05,
            peg_ratio=3.0,
            price_to_sales_ratio=10.0,
            debt_to_equity=3.0,
            current_ratio=0.5,
        ),
        line_items=_items(revenue=400_000_000, free_cash_flow=-50_000_000),
    )

    assert score_growth(growth).signal is Signal.BULLISH
    assert score_growth(contraction).signal is Signal.BEARISH
    assert score_wood(growth).score > score_wood(contraction).score
    assert score_lynch(growth).score > score_lynch(contraction).score


def test_high_leverage_and_insider_sales_reduce_quality_and_macro_scores() -> None:
    buys = (InsiderTrade(ticker="CASE", filing_date=AS_OF, transaction_shares=10_000),)
    sales = (InsiderTrade(ticker="CASE", filing_date=AS_OF, transaction_shares=-100_000),)
    healthy = _snapshot(insider_trades=buys)
    leveraged = _snapshot(
        metrics=_metrics(debt_to_equity=3.0, current_ratio=0.5),
        line_items=_items(
            total_debt=2_000_000_000,
            total_liabilities=900_000_000,
            current_assets=500_000_000,
            current_liabilities=500_000_000,
            cash_and_equivalents=5_000_000,
        ),
        insider_trades=sales,
    )

    assert score_fisher(healthy).score > score_fisher(leveraged).score
    assert score_jhunjhunwala(healthy).score > score_jhunjhunwala(leveraged).score
    assert score_druckenmiller(healthy).score > score_druckenmiller(leveraged).score


def test_news_scorer_handles_positive_negative_and_empty_news() -> None:
    positive = tuple(
        CompanyNews(
            ticker="CASE",
            title=f"Positive {index}",
            date=datetime(2025, 12, index + 1, tzinfo=UTC),
            sentiment="positive",
        )
        for index in range(6)
    )
    negative = tuple(item.model_copy(update={"sentiment": "negative"}) for item in positive)

    assert score_news(_snapshot(news=positive)).signal.value == FIXTURE["news"]["positive"]
    assert score_news(_snapshot(news=negative)).signal.value == FIXTURE["news"]["negative"]
    empty = score_news(_snapshot(news=()))
    assert empty.signal is Signal.NEUTRAL
    assert empty.confidence == 0
    assert empty.data_gaps == ["company news unavailable"]


def test_taleb_tail_risk_is_finite_and_short_history_is_an_explicit_gap() -> None:
    fat_tail = score_taleb(_snapshot(prices=_prices(FIXTURE["tail_risk"]["closes"])))
    short = score_taleb(_snapshot(prices=_prices([100, 101, 99])))

    tail = fat_tail.component("tail_risk")
    assert tail is not None
    assert tail.metrics["max_drawdown"] == pytest.approx(FIXTURE["tail_risk"]["max_drawdown"])
    assert math.isfinite(fat_tail.confidence)
    assert "insufficient price history" in short.data_gaps


def test_taleb_rejects_non_finite_prices() -> None:
    snapshot = _snapshot(prices=_prices([100, float("nan"), 101, 99, 102]))

    with pytest.raises(ValueError, match="non-finite price"):
        score_taleb(snapshot)


def test_all_growth_macro_scorers_keep_missing_inputs_visible() -> None:
    empty = CompanySnapshot(ticker="EMPTY", as_of=AS_OF)

    for scorer in (
        score_wood,
        score_lynch,
        score_fisher,
        score_jhunjhunwala,
        score_druckenmiller,
        score_taleb,
        score_growth,
        score_news,
    ):
        score = scorer(empty)
        assert score.signal is Signal.NEUTRAL
        assert score.confidence == 0
        assert score.data_gaps


def test_every_catalog_scorer_key_is_registered() -> None:
    assert registered_scorer_keys() == frozenset(
        definition.scorer_key for definition in analyst_catalog()
    )
