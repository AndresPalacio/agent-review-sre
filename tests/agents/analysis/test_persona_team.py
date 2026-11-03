from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, cast

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from langchain_core.tools import BaseTool, tool
from pydantic import PrivateAttr

from fin_graph.agents.analysis.persona_team import (
    PersonaAnalysisBundle,
    PersonaFailureCode,
    _authoritative_reasoning,
    make_persona_agent,
    make_persona_analysis_supervisor,
)
from fin_graph.agents.analysis.personas import build_persona_subagents
from fin_graph.agents.common.testing.scripted_model import ScriptedChatModel
from fin_graph.compat.ai_hedge_fund.models import LegacyHedgeFundResult
from fin_graph.domain.analysts import AnalystId, analyst_catalog, resolve_analysts
from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.market import FinancialMetrics, LineItem, Price
from fin_graph.domain.quant.common import QuantScore, Signal
from fin_graph.domain.requests.model import AnalysisRequest
from fin_graph.domain.tools import (
    Domain,
    EvidenceType,
    SideEffectLevel,
    ToolMeta,
    attach_tool_meta,
    read_tool_meta,
)
from fin_graph.integrations.analytics.engine import AnalyticsEngine
from fin_graph.integrations.context import RunContext
from fin_graph.integrations.market_data import StaticMarketData
from fin_graph.workflows.analysis.snapshots import MarketSnapshotServices


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
                    revenue_growth=0.25,
                    earnings_growth=0.2,
                    earnings_per_share_growth=0.25,
                    free_cash_flow_growth=0.2,
                    operating_income_growth=0.2,
                    return_on_equity=0.2,
                    current_ratio=2,
                    debt_to_equity=0.2,
                    gross_margin=0.55,
                    operating_margin=0.2,
                    peg_ratio=0.8,
                    price_to_sales_ratio=1,
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


class CapturingModel(ScriptedChatModel):
    _bound_tool_sets: list[frozenset[str]] = PrivateAttr(default_factory=list)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        names: set[str] = set()
        for candidate in kwargs.get("tools", ()):
            if isinstance(candidate, dict):
                function = candidate.get("function", candidate)
                if isinstance(function, dict) and function.get("name"):
                    names.add(str(function["name"]))
            elif getattr(candidate, "name", None):
                names.add(str(candidate.name))
        self._bound_tool_sets.append(frozenset(names))
        return super()._generate(messages, stop, run_manager, **kwargs)

    @property
    def bound_tool_sets(self) -> list[frozenset[str]]:
        return self._bound_tool_sets


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
        selected = cast(list[BaseTool], list(spec.get("tools", ())))
        assert PLACE_TRADE not in selected
        assert all(
            (meta := read_tool_meta(tool)) is not None
            and meta.side_effect_level is SideEffectLevel.READ_ONLY
            and bool(meta.domains.intersection(definition.domains))
            for tool in selected
        )


def test_make_persona_agent_builds_the_declared_deepagent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ScriptedChatModel(responses=[])
    spec = build_persona_subagents(
        model,
        resolve_analysts([AnalystId.FUNDAMENTALS_ANALYST]),
        [READ_FUNDAMENTALS, READ_PRICES, PLACE_TRADE],
    )[0]
    captured: dict[str, Any] = {}
    sentinel = cast(Any, object())

    def fake_create_deep_agent(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(
        "fin_graph.agents.analysis.persona_team.create_deep_agent",
        fake_create_deep_agent,
    )

    assert make_persona_agent(model, spec) is sentinel
    assert captured["name"] == "analyst_fundamentals_analyst"
    assert captured["context_schema"] is RunContext
    assert captured["checkpointer"] is False
    assert PLACE_TRADE not in captured["tools"]


async def test_compiled_persona_exposes_only_selected_read_tools() -> None:
    model = CapturingModel(responses=[AIMessage("Deterministic score acknowledged.")])
    graph = make_persona_analysis_supervisor(
        model, Registry(), RunContext(), Services(), checkpointer=False
    )

    await graph.ainvoke(
        {
            "request": AnalysisRequest(
                tickers=["CASE"],
                as_of=date(2026, 1, 1),
                analysts=(AnalystId.FUNDAMENTALS_ANALYST,),
            ).model_dump(mode="json")
        }
    )

    forbidden = {
        "delete",
        "edit_file",
        "glob",
        "grep",
        "ls",
        "read_file",
        "task",
        "write_file",
        "execute",
        "place_trade",
    }
    assert model.bound_tool_sets
    assert all(not (tool_names & forbidden) for tool_names in model.bound_tool_sets)


async def test_persona_tool_calls_are_forced_to_request_ticker_and_as_of() -> None:
    seen: list[tuple[str, str]] = []

    @tool
    async def dated_fundamentals(ticker: str, as_of: str = "") -> str:
        """Read dated fundamentals."""
        seen.append((ticker, as_of))
        return "ok"

    bounded = attach_tool_meta(
        dated_fundamentals,
        ToolMeta(
            integration="test",
            domains=frozenset({Domain.FUNDAMENTALS}),
            evidence_type=EvidenceType.FINANCIALS,
        ),
    )

    class DatedRegistry:
        def tools(self, context: RunContext) -> list[BaseTool]:
            return [bounded]

    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "future-call",
                        "name": "dated_fundamentals",
                        "args": {"ticker": "OTHER", "as_of": "2099-01-01"},
                    }
                ],
            ),
            AIMessage("done"),
        ]
    )
    graph = make_persona_analysis_supervisor(
        model, DatedRegistry(), RunContext(), Services(), checkpointer=False
    )

    await graph.ainvoke(
        {
            "request": AnalysisRequest(
                tickers=["CASE"],
                as_of=date(2026, 1, 1),
                analysts=(AnalystId.FUNDAMENTALS_ANALYST,),
            ).model_dump(mode="json")
        }
    )

    assert seen == [("CASE", "2026-01-01")]


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
            return {"messages": [AIMessage("Sell CASE or open a short position.")]}

    monkeypatch.setattr(
        "fin_graph.agents.analysis.persona_team.create_deep_agent",
        lambda **kwargs: FakeAgent(str(kwargs["name"])),
    )
    services = Services()
    graph = make_persona_analysis_supervisor(
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
    bundle = PersonaAnalysisBundle.model_validate(result["bundle"])
    legacy_result = LegacyHedgeFundResult.model_validate(result["result"])

    assert [finding.analyst_id for finding in bundle.findings] == list(request.analysts or ())
    growth, failed = bundle.findings
    assert growth.score is not None and growth.score.signal is Signal.BULLISH
    assert "sell" not in growth.reasoning.lower()
    assert "short" not in growth.reasoning.lower()
    assert growth.evidence_ids
    assert failed.score is not None
    assert failed.error is not None
    assert failed.error.code is PersonaFailureCode.NARRATION_FAILED
    assert "scripted" not in failed.error.message
    assert len(failed.error.correlation_id) == 16
    assert calls == ["analyst_growth_analyst", "analyst_news_sentiment_analyst"]
    assert services.loaded == ["CASE"]
    assert set(legacy_result.decisions) == {"CASE"}
    assert "risk_management_agent" in legacy_result.analyst_signals


@pytest.mark.parametrize(
    ("signal", "narrative"),
    [
        (Signal.BULLISH, "Sell the position."),
        (Signal.BULLISH, "Open a short."),
        (Signal.BEARISH, "Buy and stay long."),
        (Signal.NEUTRAL, "Buy the breakout."),
    ],
)
def test_directional_narratives_cannot_override_authoritative_scores(
    signal: Signal,
    narrative: str,
) -> None:
    score = QuantScore(analyst=Domain.FUNDAMENTALS, ticker="CASE", signal=signal, confidence=80)

    reasoning = _authoritative_reasoning(narrative, score)

    assert "was withheld" in reasoning


async def test_no_selection_runs_all_nineteen_in_catalog_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAgent:
        async def ainvoke(self, payload: object, **kwargs: object) -> dict[str, Any]:
            return {"messages": [AIMessage("Deterministic score acknowledged.")]}

    monkeypatch.setattr(
        "fin_graph.agents.analysis.persona_team.create_deep_agent",
        lambda **kwargs: FakeAgent(),
    )
    graph = make_persona_analysis_supervisor(
        ScriptedChatModel(responses=[]), Registry(), RunContext(), Services(), checkpointer=False
    )

    result = await graph.ainvoke(
        {
            "request": AnalysisRequest(tickers=["CASE"], as_of=date(2026, 1, 1)).model_dump(
                mode="json"
            )
        }
    )
    bundle = PersonaAnalysisBundle.model_validate(result["bundle"])

    assert [finding.analyst_id for finding in bundle.findings] == [
        definition.id for definition in analyst_catalog()
    ]


async def test_data_failure_is_typed_and_does_not_expose_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingServices(Services):
        async def company_snapshot(
            self, ticker: str, as_of: date, context: RunContext
        ) -> CompanySnapshot:
            raise RuntimeError("https://secret.example?token=do-not-leak")

    class FakeAgent:
        async def ainvoke(self, payload: object, **kwargs: object) -> dict[str, Any]:
            raise AssertionError("narration must not run without data")

    monkeypatch.setattr(
        "fin_graph.agents.analysis.persona_team.create_deep_agent", lambda **kwargs: FakeAgent()
    )
    graph = make_persona_analysis_supervisor(
        ScriptedChatModel(responses=[]),
        Registry(),
        RunContext(),
        FailingServices(),
        checkpointer=False,
    )
    result = await graph.ainvoke(
        {
            "request": AnalysisRequest(
                tickers=["CASE"],
                as_of=date(2026, 1, 1),
                analysts=(AnalystId.GROWTH_ANALYST,),
            ).model_dump(mode="json")
        }
    )

    finding = PersonaAnalysisBundle.model_validate(result["bundle"]).findings[0]
    assert finding.score is None
    assert finding.error is not None
    assert finding.error.code is PersonaFailureCode.DATA_UNAVAILABLE
    serialized = finding.model_dump_json()
    assert "secret.example" not in serialized
    assert "do-not-leak" not in serialized


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
        "fin_graph.agents.analysis.persona_team.create_deep_agent",
        lambda **kwargs: BlockingAgent(),
    )
    graph = make_persona_analysis_supervisor(
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


async def test_runtime_snapshot_service_loads_the_configured_point_in_time_provider() -> None:
    as_of = date(2026, 1, 1)
    future = date(2026, 1, 2)
    data = StaticMarketData(
        prices={
            "CASE": [
                Price(
                    time=f"{as_of.isoformat()}T00:00:00Z",
                    open=10,
                    high=10,
                    low=10,
                    close=10,
                    volume=1,
                ),
                Price(
                    time=f"{future.isoformat()}T00:00:00Z",
                    open=99,
                    high=99,
                    low=99,
                    close=99,
                    volume=1,
                ),
            ]
        },
        metrics={
            "CASE": [
                FinancialMetrics(ticker="CASE", report_period=as_of),
                FinancialMetrics(ticker="CASE", report_period=future),
            ]
        },
        line_items={
            "CASE": [
                LineItem(ticker="CASE", report_period=as_of, revenue=100),
                LineItem(ticker="CASE", report_period=future, revenue=999),
            ]
        },
        market_caps={"CASE": 1_000},
    )
    context = RunContext(
        clients={
            "analytics": AnalyticsEngine("fixtures"),
            "market_fixtures": data,
        }
    )

    snapshot = await MarketSnapshotServices().company_snapshot("CASE", as_of, context)

    assert [item.close for item in snapshot.prices] == [10]
    assert [item.report_period for item in snapshot.metrics] == [as_of]
    assert [item.value("revenue") for item in snapshot.line_items] == [100]
    assert snapshot.market_cap == 1_000
