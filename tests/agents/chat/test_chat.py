from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from fin_graph.agents.chat.supervisor import approval_description, make_chat_graph
from fin_graph.agents.common.middleware.disclaimer import DISCLAIMER
from fin_graph.agents.common.testing.scripted_model import ScriptedChatModel, tool_call_message
from fin_graph.domain.fills import OrderStatus
from fin_graph.domain.portfolio import Portfolio
from fin_graph.integrations.broker.paper.broker import PaperBroker
from fin_graph.integrations.context import RunContext
from fin_graph.memory.theses import ThesisMemory
from fin_graph.persistence.paper_ledger import open_sqlite_paper_ledger
from fin_graph.safety.approval import ApprovalPolicy
from fin_graph.safety.trading_limits import TradingLimits

ORDER = {"ticker": "MSFT", "action": "buy", "quantity": 2, "reasoning": "tesis verificada"}


class EmptyRegistry:
    def tools(self, context: RunContext) -> list[object]:
        return []


@pytest.fixture
async def broker(tmp_path: Path) -> AsyncIterator[PaperBroker]:
    async def quotes(ticker: str) -> float | None:
        return 400.0

    async with open_sqlite_paper_ledger(tmp_path / "paper.sqlite") as store:
        yield PaperBroker(
            store=store,
            quotes=quotes,
            limits=TradingLimits(max_order_notional=5_000, max_position_pct=1.0),
            initial=Portfolio(cash=10_000.0),
            clock=lambda: datetime(2024, 6, 28, 14, 0, tzinfo=UTC),
            market_open=lambda now: True,
        )


def chat(model: ScriptedChatModel, store: InMemoryStore | None = None, **kwargs: Any) -> Any:
    return make_chat_graph(
        model,
        EmptyRegistry(),
        RunContext(),
        ThesisMemory(store or InMemoryStore()),
        policy=ApprovalPolicy(environment="prod"),
        checkpointer=InMemorySaver(),
        **kwargs,
    )


async def test_chat_keeps_the_thread_and_recalls_a_verified_thesis() -> None:
    store = InMemoryStore()
    await ThesisMemory(store).save_thesis(
        "run-1:MSFT",
        ticker="MSFT",
        as_of=date(2024, 3, 28),
        stance="bullish",
        category="quality_compounder",
        confidence=70,
        entry_price=420.0,
        summary="ROE alto y estable con foso.",
        evidence_ids=["ev_1"],
    )
    model = ScriptedChatModel(
        responses=[
            tool_call_message("search_theses", {"ticker": "MSFT"}, "m1"),
            AIMessage("Tesis previa: quality compounder (ROE alto)."),
            AIMessage("Seguimos en el mismo hilo."),
        ]
    )
    graph = chat(model, store)
    config = {"configurable": {"thread_id": "chat-1"}}

    first = await graph.ainvoke({"messages": [HumanMessage("¿Qué pensábamos de MSFT?")]}, config)
    second = await graph.ainvoke({"messages": [HumanMessage("Gracias")]}, config)

    assert "quality_compounder" in str(first["messages"][-2].content)
    assert len(second["messages"]) > len(first["messages"])


async def test_an_order_waits_for_a_human_then_fills_once(broker: PaperBroker) -> None:
    context = RunContext(clients={"paper_broker": broker})
    model = ScriptedChatModel(
        responses=[tool_call_message("propose_order", ORDER, "o1"), AIMessage("Orden ejecutada.")]
    )
    graph = chat(model)
    config = {"configurable": {"thread_id": "orders"}}

    paused = await graph.ainvoke(
        {"messages": [HumanMessage("Compra 2 MSFT")]}, config, context=context
    )
    request = paused["__interrupt__"][0].value["action_requests"][0]
    assert request["name"] == "propose_order" and request["description"].startswith("Comprar 2")
    assert await broker.orders() == []

    approver = RunContext(clients={"paper_broker": broker}, actor="trader-1", role="approver")
    done = await graph.ainvoke(
        Command(resume={"decisions": [{"type": "approve"}]}), config, context=approver
    )

    [order] = await broker.orders()
    assert order.status is OrderStatus.FILLED and order.order.quantity == 2
    assert json.loads(str(done["messages"][-2].content))["verified"] is True
    assert DISCLAIMER in done["messages"][-1].text
    assert (await broker.portfolio()).position("MSFT").long == 2


async def test_a_rejected_order_never_reaches_the_broker(broker: PaperBroker) -> None:
    context = RunContext(clients={"paper_broker": broker}, actor="trader-1", role="approver")
    model = ScriptedChatModel(
        responses=[tool_call_message("propose_order", ORDER, "o1"), AIMessage("Rechazada.")]
    )
    graph = chat(model)
    config = {"configurable": {"thread_id": "reject"}}

    await graph.ainvoke({"messages": [HumanMessage("Compra 2 MSFT")]}, config, context=context)
    await graph.ainvoke(
        Command(resume={"decisions": [{"type": "reject"}]}), config, context=context
    )

    assert await broker.orders() == []


async def test_preview_reports_limits_and_changes_nothing(broker: PaperBroker) -> None:
    context = RunContext(clients={"paper_broker": broker})
    oversized = {"ticker": "MSFT", "action": "buy", "quantity": 20}
    model = ScriptedChatModel(
        responses=[tool_call_message("preview_order", oversized, "p1"), AIMessage("Bloqueada.")]
    )

    result = await chat(model).ainvoke(
        {"messages": [HumanMessage("¿Puedo comprar 20?")]},
        {"configurable": {"thread_id": "preview"}},
        context=context,
    )

    preview = str(result["messages"][-2].content)
    assert "BLOQUEADA" in preview and "max_order_notional" in preview
    assert "__interrupt__" not in result and await broker.orders() == []


async def test_analyze_runs_the_analysis_with_the_live_paper_book(broker: PaperBroker) -> None:
    from tests.domain.test_analysis_report import _report

    class Analysis:
        def __init__(self) -> None:
            self.inputs: list[dict[str, Any]] = []

        async def ainvoke(self, value: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            self.inputs.append(value)
            return {"report": _report().model_dump(mode="json")}

    analysis = Analysis()
    model = ScriptedChatModel(
        responses=[
            tool_call_message(
                "analyze",
                {
                    "tickers": "DEEP, DIST",
                    "horizon": "long",
                    "analysts": "ben_graham,valuation_analyst",
                },
                "a1",
            ),
            AIMessage("Según el análisis, DEEP podría ser value; es una hipótesis."),
        ]
    )
    result = await chat(model, analysis=analysis).ainvoke(
        {"messages": [HumanMessage("Analiza DEEP y DIST")]},
        {"configurable": {"thread_id": "analysis"}},
        context=RunContext(clients={"paper_broker": broker}),
    )

    [sent] = analysis.inputs
    assert sent["raw_request"]["tickers"] == ["DEEP", "DIST"]
    assert sent["raw_request"]["analysts"] == ["ben_graham", "valuation_analyst"]
    assert sent["portfolio"]["cash"] == 10_000.0
    summary = json.loads(str(result["messages"][-2].content))
    assert summary["tickers"][0]["ticker"] == "DEEP"


async def test_analyze_accepts_the_compatibility_persona_bundle(broker: PaperBroker) -> None:
    from fin_graph.agents.analysis.persona_team import PersonaAnalysisBundle, PersonaFinding
    from fin_graph.domain.analysts import AnalystId
    from fin_graph.domain.quant.common import QuantScore, Signal
    from fin_graph.domain.requests.model import AnalysisRequest
    from fin_graph.domain.tools import Domain

    bundle = PersonaAnalysisBundle(
        request=AnalysisRequest(tickers=["MSFT"], as_of=date(2026, 1, 1)),
        findings=(
            PersonaFinding(
                analyst_id=AnalystId.BILL_ACKMAN,
                ticker="MSFT",
                score=QuantScore(
                    analyst=Domain.FUNDAMENTALS,
                    ticker="MSFT",
                    signal=Signal.BULLISH,
                    confidence=80,
                ),
                reasoning="Deterministic quality score.",
                evidence_ids=("metric:2025-12-31",),
            ),
        ),
    )

    class Analysis:
        async def ainvoke(self, value: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            return {"bundle": bundle.model_dump(mode="json")}

    model = ScriptedChatModel(
        responses=[
            tool_call_message("analyze", {"tickers": "MSFT"}, "a1"),
            AIMessage("MSFT has a bullish deterministic finding."),
        ]
    )
    result = await chat(model, analysis=Analysis()).ainvoke(
        {"messages": [HumanMessage("Analyze MSFT")]},
        {"configurable": {"thread_id": "compat-analysis"}},
        context=RunContext(clients={"paper_broker": broker}),
    )

    summary = json.loads(str(result["messages"][-2].content))
    analyst = summary["tickers"][0]["analysts"][0]
    assert analyst["analyst_id"] == "bill_ackman"
    assert analyst["signal"] == "bullish"
    assert analyst["evidence_ids"] == ["metric:2025-12-31"]


def test_the_approver_reads_the_concrete_order_and_whether_it_was_analyzed() -> None:
    from langchain_core.messages import ToolMessage

    blind = approval_description({"name": "propose_order", "args": ORDER}, {}, None)
    analyzed_state = {
        "messages": [
            ToolMessage(
                content='{"tickers":[{"ticker":"MSFT","stance":"bullish"}]}',
                tool_call_id="a1",
                name="analyze",
            )
        ]
    }
    informed = approval_description({"name": "propose_order", "args": ORDER}, analyzed_state, None)
    cancel = approval_description(
        {"name": "cancel_paper_order", "args": {"client_order_id": "ord_1"}}, {}, None
    )

    assert blind.startswith("Comprar 2 MSFT") and "no se mueve dinero real" in blind
    assert "AVISO: no hay un análisis de MSFT" in blind
    assert "AVISO" not in informed
    assert "ord_1" in cancel
