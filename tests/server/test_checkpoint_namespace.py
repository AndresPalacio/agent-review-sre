from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from tests.server.finance_app import (
    REQUEST,
    FixtureTools,
    fixture_context,
    make_storage,
    paper_broker,
)
from tests.server.test_orders_api import ORDER

from fin_graph.agents.chat.supervisor import make_chat_graph
from fin_graph.agents.common.testing.scripted_model import ScriptedChatModel, tool_call_message
from fin_graph.integrations.registry import IntegrationRegistry
from fin_graph.memory.theses import ThesisMemory
from fin_graph.runtime.analysis import make_analysis_graph
from fin_graph.safety.approval import ApprovalPolicy
from fin_graph.server.app import _retention_cycle, create_app
from fin_graph.server.manager import RunManager
from fin_graph.server.storage import CheckpointThreadRow
from fin_graph.trading.order_desk import OrderDesk
from fin_graph.workflows.analysis.services import UnavailableAnalysisServices
from fin_graph.workflows.orders.graph import make_order_graph


async def test_shared_saver_keeps_same_thread_id_separate_for_all_graphs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread_id = "00000000-0000-0000-0000-000000000001"
    monkeypatch.setattr("fin_graph.server.app.uuid4", lambda: UUID(thread_id))
    engine, storage = await make_storage(tmp_path / "namespaces.sqlite")
    registry = IntegrationRegistry([], env_file=None)
    saver = InMemorySaver()
    context = fixture_context()
    analysis = make_analysis_graph(
        UnavailableAnalysisServices(), FixtureTools(), checkpointer=saver
    )
    manager = RunManager(analysis, storage, context)
    policy = ApprovalPolicy(environment="prod")
    chat = make_chat_graph(
        ScriptedChatModel(responses=[AIMessage("Chat response")]),
        registry,
        context(),
        ThesisMemory(InMemoryStore()),
        policy=policy,
        checkpointer=saver,
    )
    broker = paper_broker(engine)
    orders = make_order_graph(OrderDesk(broker), policy, checkpointer=saver)
    app = create_app(
        manager=manager,
        registry=registry,
        chat_graph=chat,
        order_graph=orders,
        paper=broker,
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        run = await manager.submit(REQUEST, "json", thread_id=thread_id)
        await manager.wait(run["run_id"])
        analysis_before = await analysis.aget_state(manager._config(thread_id))
        assert analysis_before.values.get("report")

        chat_response = await client.post(
            f"/v1/chat/threads/{thread_id}/messages",
            json={"message": "What happened?"},
            headers={"x-api-key": "operator-key"},
        )
        assert chat_response.status_code == 200, chat_response.text
        assert "Chat response" in chat_response.text

        order_response = await client.post(
            "/v1/orders",
            json=ORDER,
            headers={"x-api-key": "operator-key"},
        )
        assert order_response.status_code == 202, order_response.text
        assert order_response.json()["thread_id"] == thread_id
        assert order_response.json()["status"] == "awaiting_approval"

        analysis_after = await analysis.aget_state(manager._config(thread_id))
        chat_state = await chat.aget_state({"configurable": {"thread_id": f"chat:{thread_id}"}})
        order_state = await orders.aget_state(
            {"configurable": {"thread_id": f"orders:{thread_id}"}}
        )
        assert analysis_after.values["report"] == analysis_before.values["report"]
        assert analysis_after.values.get("messages") is None
        assert chat_state.values["messages"][-1].text == "Chat response"
        assert chat_state.values.get("tool") is None
        assert order_state.values["order"]["ticker"] == "DEEP"
        assert order_state.values.get("messages") is None
        legacy_id = f"remediation:{thread_id}"
        await analysis.ainvoke(
            {"raw_request": REQUEST, "source_hint": "json"},
            {"configurable": {"thread_id": legacy_id}},
            context=context(),
        )
        async with storage.sessions.begin() as session:
            session.add(
                CheckpointThreadRow(
                    thread_id=thread_id,
                    kind="remediation",
                    touched_at=datetime.now(UTC),
                )
            )
        await storage.record_watch_result("deep", "normal", {}, datetime.now(UTC))
        assert await storage.claim_gateway_event("slack", "retention-event")
        await _retention_cycle(
            manager,
            saver,
            datetime.now(UTC) + timedelta(days=1),
            app.state.chat_locks,
            app.state.order_locks,
        )
        for checkpoint_id in (
            thread_id,
            f"chat:{thread_id}",
            f"orders:{thread_id}",
            legacy_id,
        ):
            assert await saver.aget_tuple({"configurable": {"thread_id": checkpoint_id}}) is None
        assert await storage.latest_run(thread_id) is None
        assert await storage.recent_watch_results(datetime.min.replace(tzinfo=UTC)) == []
        assert await storage.claim_gateway_event("slack", "retention-event")
    await engine.dispose()


async def test_chat_analyze_tool_does_not_replace_the_persisted_analysis() -> None:
    registry = IntegrationRegistry([], env_file=None)
    saver = InMemorySaver()
    context = fixture_context()()
    analysis = make_analysis_graph(
        UnavailableAnalysisServices(), FixtureTools(), checkpointer=saver
    )
    chat_analysis = make_analysis_graph(
        UnavailableAnalysisServices(),
        FixtureTools(),
        checkpointer=False,
    )
    thread_id = "same-id"
    root_config = {"configurable": {"thread_id": thread_id}}
    chat_config = {"configurable": {"thread_id": f"chat:{thread_id}"}}
    await analysis.ainvoke(
        {"raw_request": REQUEST, "source_hint": "json"}, root_config, context=context
    )
    before = await saver.aget_tuple(root_config)
    assert before is not None

    chat = make_chat_graph(
        ScriptedChatModel(
            responses=[
                tool_call_message("analyze", {"tickers": "MSFT"}, "call-1"),
                AIMessage("Analysis complete"),
            ]
        ),
        registry,
        context,
        ThesisMemory(InMemoryStore()),
        policy=ApprovalPolicy(environment="prod"),
        analysis=chat_analysis,
        checkpointer=saver,
    )
    await chat.ainvoke(
        {"messages": [HumanMessage("Analyze MSFT")]},
        chat_config,
        context=context,
    )

    after = await saver.aget_tuple(root_config)
    assert after is not None
    assert after.checkpoint["id"] == before.checkpoint["id"]
    assert await saver.aget_tuple(chat_config) is not None
    assert set(saver.storage[f"chat:{thread_id}"]) == {""}
