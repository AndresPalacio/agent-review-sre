from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import create_async_engine

from sre_graph.agents.testing.scripted_model import ScriptedChatModel, tool_call_message
from sre_graph.graphs.chat.graph import make_chat_graph
from sre_graph.graphs.investigation.graph import make_investigation_graph
from sre_graph.graphs.investigation.services import UnavailableServices
from sre_graph.graphs.remediation.graph import ActionCatalog, ActionSpec, make_remediation_graph
from sre_graph.integrations.context import RunContext
from sre_graph.integrations.registry import IntegrationRegistry
from sre_graph.memory.service import IncidentMemory
from sre_graph.safety.approval import ApprovalPolicy
from sre_graph.server.app import _retention_cycle, create_app
from sre_graph.server.manager import RunManager
from sre_graph.server.storage import Base, Storage


async def test_shared_saver_keeps_same_thread_id_separate_for_all_graphs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Args(BaseModel):
        replicas: int

    async def prepare(args: Args, context: RunContext) -> dict[str, int]:
        return {"replicas": args.replicas}

    async def execute(args: Args, key: str, context: RunContext) -> dict[str, int]:
        return {"replicas": args.replicas}

    async def verify(args: Args, context: RunContext) -> bool:
        return True

    thread_id = "00000000-0000-0000-0000-000000000001"
    monkeypatch.setattr("sre_graph.server.app.uuid4", lambda: UUID(thread_id))
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'namespaces.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    await storage.put_key("operator-key", "operator")
    registry = IntegrationRegistry([], env_file=None)
    saver = InMemorySaver()
    investigation = make_investigation_graph(UnavailableServices(), registry, checkpointer=saver)
    manager = RunManager(investigation, storage, RunContext)
    policy = ApprovalPolicy(environment="prod")
    chat = make_chat_graph(
        ScriptedChatModel(responses=[AIMessage("Chat response")]),
        registry,
        RunContext(),
        IncidentMemory(InMemoryStore()),
        ActionCatalog([]),
        policy=policy,
        checkpointer=saver,
    )
    remediation = make_remediation_graph(
        ActionCatalog([ActionSpec("scale", Args, prepare, execute, verify)]),
        policy,
        checkpointer=saver,
    )
    app = create_app(
        manager=manager, registry=registry, chat_graph=chat, remediation_graph=remediation
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        run = await manager.submit("CPU high", None, thread_id=thread_id)
        await manager.wait(run["run_id"])
        investigation_before = await investigation.aget_state(manager._config(thread_id))
        assert investigation_before.values.get("report")

        chat_response = await client.post(
            f"/v1/chat/threads/{thread_id}/messages",
            json={"message": "What happened?"},
            headers={"x-api-key": "operator-key"},
        )
        assert chat_response.status_code == 200, chat_response.text
        assert "Chat response" in chat_response.text

        remediation_response = await client.post(
            "/v1/remediations",
            json={"tool": "scale", "args": {"replicas": 2}},
            headers={"x-api-key": "operator-key"},
        )
        assert remediation_response.status_code == 202, remediation_response.text
        assert remediation_response.json()["thread_id"] == thread_id
        assert remediation_response.json()["status"] == "awaiting_approval"

        investigation_after = await investigation.aget_state(manager._config(thread_id))
        chat_state = await chat.aget_state({"configurable": {"thread_id": f"chat:{thread_id}"}})
        remediation_state = await remediation.aget_state(
            {"configurable": {"thread_id": f"remediation:{thread_id}"}}
        )
        assert investigation_after.values["report"] == investigation_before.values["report"]
        assert investigation_after.values.get("messages") is None
        assert chat_state.values["messages"][-1].text == "Chat response"
        assert chat_state.values.get("tool") is None
        assert remediation_state.values["tool"] == "scale"
        assert remediation_state.values.get("messages") is None
        await storage.record_health_result("cpu", "healthy", {}, datetime.now(UTC))
        assert await storage.claim_gateway_event("slack", "retention-event")
        await _retention_cycle(
            manager,
            saver,
            datetime.now(UTC) + timedelta(days=1),
            app.state.chat_locks,
            app.state.remediation_locks,
        )
        for checkpoint_id in (thread_id, f"chat:{thread_id}", f"remediation:{thread_id}"):
            assert await saver.aget_tuple({"configurable": {"thread_id": checkpoint_id}}) is None
        assert await storage.latest_run(thread_id) is None
        assert await storage.recent_health_results(datetime.min.replace(tzinfo=UTC)) == []
        assert await storage.claim_gateway_event("slack", "retention-event")
    await engine.dispose()


async def test_chat_investigate_tool_does_not_replace_the_persisted_investigation() -> None:
    registry = IntegrationRegistry([], env_file=None)
    saver = InMemorySaver()
    services = UnavailableServices()
    investigation = make_investigation_graph(services, registry, checkpointer=saver)
    chat_investigation = make_investigation_graph(
        services,
        registry,
        checkpointer=False,  # type: ignore[arg-type]
    )
    thread_id = "same-id"
    root_config = {"configurable": {"thread_id": thread_id}}
    chat_config = {"configurable": {"thread_id": f"chat:{thread_id}"}}
    await investigation.ainvoke(
        {"raw_alert": "CPU high", "user_requested": True}, root_config, context=RunContext()
    )
    before = await saver.aget_tuple(root_config)
    assert before is not None

    chat = make_chat_graph(
        ScriptedChatModel(
            responses=[
                tool_call_message("investigate", {"alert": "Memory pressure"}, "call-1"),
                AIMessage("Investigation complete"),
            ]
        ),
        registry,
        RunContext(),
        IncidentMemory(InMemoryStore()),
        ActionCatalog([]),
        policy=ApprovalPolicy(environment="prod"),
        investigation=chat_investigation,
        checkpointer=saver,
    )
    await chat.ainvoke(
        {"messages": [HumanMessage("Investigate memory pressure")]},
        chat_config,
        context=RunContext(),
    )

    after = await saver.aget_tuple(root_config)
    assert after is not None
    assert after.checkpoint["id"] == before.checkpoint["id"]
    assert await saver.aget_tuple(chat_config) is not None
    assert set(saver.storage[f"chat:{thread_id}"]) == {""}
