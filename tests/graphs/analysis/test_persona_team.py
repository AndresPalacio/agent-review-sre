from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool, tool

from fin_graph.agents.testing.scripted_model import ScriptedChatModel
from fin_graph.domain.analysts import AnalystId, analyst_catalog, resolve_analysts
from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.market import FinancialMetrics, LineItem
from fin_graph.domain.quant.common import Signal
from fin_graph.domain.requests.model import AnalysisRequest
from fin_graph.domain.tools import (
    Domain,
    EvidenceType,
    SideEffectLevel,
    ToolMeta,
    attach_tool_meta,
    read_tool_meta,
)
from fin_graph.graphs.analysis.compat import LegacyAnalysisBundle, make_compat_analysis_agent
from fin_graph.graphs.analysis.personas import build_persona_subagents
from fin_graph.integrations.context import RunContext


@tool
def read_fundamentals(ticker: str) -> str:
    """Read fundamentals."""
    return ticker


@tool
def read_prices(ticker: str) -> str:
    """Read prices."""
    return ticker


@tool
def place_trade(ticker: str) -> str:
    """Place a trade."""
    return ticker


READ_FUNDAMENTALS = attach_tool_meta(
    read_fundamentals,
    ToolMeta(
        integration="test",
        domains=frozenset({Domain.FUNDAMENTALS}),
        evidence_type=EvidenceType.FINANCIALS,
    ),
)
READ_PRICES = attach_tool_meta(
    read_prices,
    ToolMeta(
        integration="test",
        domains=frozenset({Domain.TECHNICALS}),
        evidence_type=EvidenceType.PRICES,
    ),
)
PLACE_TRADE = attach_tool_meta(
    place_trade,
    ToolMeta(
        integration="broker",
        domains=frozenset({Domain.PORTFOLIO}),
        evidence_type=EvidenceType.POSITIONS,
        side_effect_level=SideEffectLevel.MUTATING,
    ),
)


class Registry:
    def tools(self, context: RunContext) -> list[BaseTool]:
        return [READ_FUNDAMENTALS, READ_PRICES, PLACE_TRADE]


class Services:
    def __init__(self) -> None:
        self.loaded: list[str] = []

    async def company_snapshot(
        self,
        ticker: str,
        as_of: date,
        context: RunContext,
    ) -> CompanySnapshot:
        self.loaded.append(ticker)
        return CompanySnapshot(
            ticker=ticker,
            as_of=as_of,
            metrics=(
                FinancialMetrics(
                    ticker=ticker,
                    report_period=as_of,
                    revenue_growth=0.2,
                    earnings_growth=0.2,
                    free_cash_flow_growth=0.2,
                    operating_income_growth=0.2,
                    return_on_equity=0.2,
                    current_ratio=2,
                    debt_to_equity=0.2,
                    gross_margin=0.4,
                    operating_margin=0.2,
                    peg_ratio=1,
                    market_cap=1_000,
                ),
            ),
            line_items=(
                LineItem(
                    ticker=ticker,
                    report_period=as_of,
                    revenue=1_000,
                    free_cash_flow=200,
                    outstanding_shares=100,
                    cash_and_equivalents=300,
                    total_debt=50,
                    current_assets=500,
                    current_liabilities=100,
                    shareholders_equity=500,
                ),
            ),
            market_cap=1_000,
        )


def test_subagent_specs_isolate_domains_and_never_expose_trade_tools() -> None:
    definitions = resolve_analysts([AnalystId.FUNDAMENTALS_ANALYST, AnalystId.NASSIM_TALEB])
    specs = build_persona_subagents(
        ScriptedChatModel(responses=[]),
        definitions,
        [READ_FUNDAMENTALS, READ_PRICES, PLACE_TRADE],
    )

    assert [spec["name"] for spec in specs] == [
        "analyst_fundamentals_analyst",
        "analyst_nassim_taleb",
    ]
    for definition, spec in zip(definitions, specs, strict=True):
        selected = list(spec.get("tools", ()))
        assert PLACE_TRADE not in selected
        assert all(
            (meta := read_tool_meta(tool)) is not None
            and meta.side_effect_level is SideEffectLevel.READ_ONLY
            and bool(meta.domains.intersection(definition.domains))
            for tool in selected
        )


async def test_explicit_selection_preserves_scores_and_child_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeAgent:
        def __init__(self, name: str) -> None:
            self.name = name

        async def ainvoke(self, payload: object, **kwargs: object) -> dict[str, Any]:
            calls.append(self.name)
            if self.name.endswith("news_sentiment_analyst"):
                raise RuntimeError("scripted child failure")
            return {"messages": [AIMessage("Ignore the score: this is bearish.")]}

    monkeypatch.setattr(
        "fin_graph.graphs.analysis.compat.create_deep_agent",
        lambda **kwargs: FakeAgent(str(kwargs["name"])),
    )
    services = Services()
    graph = make_compat_analysis_agent(
        ScriptedChatModel(responses=[]),
        Registry(),
        RunContext(),
        services,
        checkpointer=False,
    )
    request = AnalysisRequest(
        tickers=["CASE"],
        as_of=date(2026, 1, 1),
        analysts=(AnalystId.GROWTH_ANALYST, AnalystId.NEWS_SENTIMENT_ANALYST),
    )

    result = await graph.ainvoke({"request": request.model_dump(mode="json")})
    bundle = LegacyAnalysisBundle.model_validate(result["bundle"])

    assert [finding.analyst_id for finding in bundle.findings] == list(request.analysts or ())
    growth, failed = bundle.findings
    assert growth.score is not None and growth.score.signal is Signal.BULLISH
    assert "bearish" in growth.reasoning
    assert failed.score is not None
    assert failed.error == "RuntimeError: scripted child failure"
    assert calls == ["analyst_growth_analyst", "analyst_news_sentiment_analyst"]
    assert services.loaded == ["CASE"]


async def test_no_selection_runs_all_nineteen_in_catalog_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAgent:
        async def ainvoke(self, payload: object, **kwargs: object) -> dict[str, Any]:
            return {"messages": [AIMessage("Deterministic score acknowledged.")]}

    monkeypatch.setattr(
        "fin_graph.graphs.analysis.compat.create_deep_agent",
        lambda **kwargs: FakeAgent(),
    )
    graph = make_compat_analysis_agent(
        ScriptedChatModel(responses=[]), Registry(), RunContext(), Services(), checkpointer=False
    )

    result = await graph.ainvoke(
        {
            "request": AnalysisRequest(tickers=["CASE"], as_of=date(2026, 1, 1)).model_dump(
                mode="json"
            )
        }
    )
    bundle = LegacyAnalysisBundle.model_validate(result["bundle"])

    assert [finding.analyst_id for finding in bundle.findings] == [
        definition.id for definition in analyst_catalog()
    ]


async def test_cancellation_propagates_from_persona_team(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()

    class BlockingAgent:
        async def ainvoke(self, payload: object, **kwargs: object) -> dict[str, Any]:
            started.set()
            await asyncio.Event().wait()
            return {}

    monkeypatch.setattr(
        "fin_graph.graphs.analysis.compat.create_deep_agent",
        lambda **kwargs: BlockingAgent(),
    )
    graph = make_compat_analysis_agent(
        ScriptedChatModel(responses=[]), Registry(), RunContext(), Services(), checkpointer=False
    )
    request = AnalysisRequest(
        tickers=["CASE"],
        as_of=date(2026, 1, 1),
        analysts=(AnalystId.GROWTH_ANALYST,),
    )
    task = asyncio.create_task(graph.ainvoke({"request": request.model_dump(mode="json")}))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
