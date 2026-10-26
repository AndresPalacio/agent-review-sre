from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import ToolMessage
from tests.domain.quant.synthetic import (
    COMPANIES,
    bars,
    company_metrics,
    insider_trades,
    line_items,
    news,
)

from fin_graph.domain.portfolio import Portfolio
from fin_graph.domain.quant.common import QuantScore, Signal
from fin_graph.domain.tools import ANALYST_DOMAINS, Domain, EvidenceType, read_tool_meta
from fin_graph.integrations.analytics.engine import AnalyticsEngine
from fin_graph.integrations.analytics.integration import (
    AnalyticsIntegration,
    MarketFixturesIntegration,
)
from fin_graph.integrations.analytics.tools import ANALYTICS_TOOLS, COMPUTE_TOOLS
from fin_graph.integrations.base import IntegrationError
from fin_graph.integrations.catalog import default_registry
from fin_graph.integrations.context import RunContext
from fin_graph.integrations.market_data import RunMemo, StaticMarketData, load_market_fixtures
from fin_graph.integrations.tooling import invoke_tool

AS_OF = date(2024, 3, 8)


def market() -> StaticMarketData:
    ticker, _, market_cap = COMPANIES["deep_value"]
    return StaticMarketData(
        prices={ticker: bars(n=320, seed=3)},
        metrics={ticker: company_metrics("deep_value")},
        line_items={ticker: line_items("deep_value")},
        insider_trades={ticker: insider_trades("TSLA")},
        news={ticker: news("TSLA", rotate_labels=True)},
        market_caps={ticker: market_cap},
    )


class Broken(StaticMarketData):
    """Every metrics call fails, everything else answers."""

    async def financial_metrics(self, *args: Any, **kwargs: Any) -> Any:
        raise IntegrationError("HTTP GET /financial-metrics/ returned 503")


def context(data: StaticMarketData | None = None) -> RunContext:
    return RunContext(
        clients={"analytics": AnalyticsEngine("fixtures"), "market_fixtures": data or market()}
    )


async def call(tool_name: str, args: dict[str, Any], run: RunContext) -> ToolMessage:
    tool = next(item for item in ANALYTICS_TOOLS if item.name == tool_name)
    return await invoke_tool(tool, args, run, call_id="call-1")


@pytest.mark.parametrize("analyst", ANALYST_DOMAINS)
async def test_every_analyst_scores_from_the_provider(analyst: Domain) -> None:
    score = await AnalyticsEngine("fixtures").score(analyst, "DEEP", AS_OF, market())

    assert score.analyst is analyst and score.ticker == "DEEP"
    assert not score.degraded


async def test_scores_are_cached_per_run() -> None:
    engine, data = AnalyticsEngine("fixtures"), market()

    first, second = await asyncio.gather(
        engine.score(Domain.GRAHAM, "DEEP", AS_OF, data),
        engine.score(Domain.GRAHAM, "DEEP", AS_OF, data),
    )

    assert first is second


async def test_a_failed_fetch_is_a_gap_and_total_failure_raises() -> None:
    engine = AnalyticsEngine("fixtures")
    data = Broken(line_items={"DEEP": line_items("deep_value")}, market_caps={"DEEP": 0.6e9})

    graham = await engine.score(Domain.GRAHAM, "DEEP", AS_OF, data)

    assert graham.data_gaps[0].startswith("metrics unavailable: HTTP GET")
    assert graham.score is not None
    with pytest.raises(IntegrationError, match="fundamentals for DEEP"):
        await engine.score(Domain.FUNDAMENTALS, "DEEP", AS_OF, data)


async def test_position_limits_price_the_held_tickers_too() -> None:
    portfolio = Portfolio(cash=50_000)

    limits = await AnalyticsEngine("fixtures").position_limits(
        ["DEEP", "NONE"], AS_OF, market(), portfolio
    )

    assert limits["DEEP"].max_shares > 0
    assert limits["NONE"].remaining_limit == 0


async def test_compute_tools_return_the_score_as_artifact() -> None:
    message = await call("compute_valuation", {"ticker": "deep", "as_of": "2024-03-08"}, context())

    score = QuantScore.model_validate(message.artifact)
    content = json.loads(message.content)  # type: ignore[arg-type]
    assert score.signal is Signal.BULLISH and content["signal"] == "bullish"
    assert {item["name"] for item in content["components"]} >= {"dcf", "owner_earnings"}


async def test_data_tools_summarise_for_the_model_and_keep_rows() -> None:
    message = await call(
        "get_prices", {"ticker": "DEEP", "start": "2024-01-01", "end": "2024-03-08"}, context()
    )

    summary = json.loads(message.content)  # type: ignore[arg-type]
    assert summary["bars"] == len(message.artifact) and len(summary["recent"]) == 10
    assert summary["last"]["day"] <= "2024-03-08"


async def test_tool_errors_reach_the_model_as_text() -> None:
    bad_date = await call("compute_graham", {"ticker": "DEEP", "as_of": "yesterday"}, context())
    bad_ticker = await call("compute_graham", {"ticker": "not a ticker!"}, context())
    no_provider = await call(
        "compute_graham", {"ticker": "DEEP"}, RunContext(clients={"analytics": AnalyticsEngine()})
    )

    assert "YYYY-MM-DD" in str(bad_date.content)
    assert "not a ticker" in str(bad_ticker.content)
    assert "yahoo is unavailable" in str(no_provider.content)


def test_tool_metadata_routes_each_analyst() -> None:
    assert set(COMPUTE_TOOLS) == set(ANALYST_DOMAINS)
    for analyst, tool in COMPUTE_TOOLS.items():
        meta = read_tool_meta(tool)
        assert meta is not None and meta.domains == {analyst} and meta.integration == "analytics"
    prices = read_tool_meta(next(item for item in ANALYTICS_TOOLS if item.name == "get_prices"))
    assert prices is not None and prices.evidence_type is EvidenceType.PRICES


def test_analytics_follows_its_provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert AnalyticsIntegration().load_settings(None) is not None  # Yahoo is on by default
    monkeypatch.setenv("YAHOO_ENABLED", "false")
    assert AnalyticsIntegration().load_settings(None) is None
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "fixtures")
    assert AnalyticsIntegration().load_settings(None) is None
    monkeypatch.setenv("MARKET_FIXTURES_DIR", str(tmp_path))
    assert AnalyticsIntegration().load_settings(None) is not None


def test_default_registry_offers_market_tools_out_of_the_box() -> None:
    registry = default_registry(env_file=None)

    run = registry.run_context()
    names = {tool.name for tool in registry.tools(run)}

    assert {"yahoo", "analytics"} <= set(run.clients)
    assert names == {tool.name for tool in ANALYTICS_TOOLS}
    assert {tool.name for tool in registry.tools(run, [Domain.GRAHAM])} >= {"compute_graham"}


def test_market_fixtures_load_from_a_directory(tmp_path: Path) -> None:
    folder = tmp_path / "deep"
    folder.mkdir()
    (folder / "prices.json").write_text(
        json.dumps({"prices": [bar.model_dump(mode="json") for bar in bars(n=5)]}), encoding="utf-8"
    )
    (folder / "market_cap.json").write_text("600000000", encoding="utf-8")

    data = load_market_fixtures(tmp_path)

    assert data.tickers == ["DEEP"]
    assert asyncio.run(data.market_cap("DEEP", AS_OF)) == 6e8
    (folder / "news.json").write_text('[{"title": 1}]', encoding="utf-8")
    with pytest.raises(IntegrationError, match=r"malformed market fixture news.json"):
        load_market_fixtures(tmp_path)
    with pytest.raises(IntegrationError, match="not found"):
        load_market_fixtures(tmp_path / "missing")


def test_fixtures_integration_needs_its_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    integration = MarketFixturesIntegration()
    assert integration.load_settings(None) is None
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "fixtures")
    settings = integration.load_settings(None)
    assert settings is not None
    with pytest.raises(ValueError, match="MARKET_FIXTURES_DIR"):
        integration.build_client(settings)


async def test_static_market_data_is_point_in_time() -> None:
    data = market()

    assert all(bar.day <= AS_OF for bar in await data.prices("DEEP", date(2024, 1, 1), AS_OF))
    assert all(
        item.report_period <= date(2022, 1, 1)
        for item in await data.line_items("DEEP", [], date(2022, 1, 1))
    )
    assert await data.insider_trades("DEEP", date(2000, 1, 1)) == []


async def test_run_memo_coalesces_and_survives_a_cancelled_caller() -> None:
    memo, calls = RunMemo(), []

    async def load() -> int:
        calls.append(1)
        await asyncio.sleep(0.01)
        return 7

    waiter = asyncio.ensure_future(memo.get("k", load))
    await asyncio.sleep(0)
    waiter.cancel()
    assert await memo.get("k", load) == 7
    assert calls == [1]
