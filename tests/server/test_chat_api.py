from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from sqlalchemy.ext.asyncio import create_async_engine

from fin_graph.agents.testing.scripted_model import ScriptedChatModel, tool_call_message
from fin_graph.graphs.chat.graph import make_chat_graph
from fin_graph.graphs.investigation.graph import make_investigation_graph
from fin_graph.graphs.investigation.services import UnavailableServices
from fin_graph.integrations.context import RunContext
from fin_graph.integrations.registry import IntegrationRegistry
from fin_graph.memory.theses import ThesisMemory
from fin_graph.safety.approval import ApprovalPolicy
from fin_graph.server.app import create_app
from fin_graph.server.manager import RunManager
from fin_graph.server.storage import Base, Storage


async def test_chat_sse_requires_operator_and_keeps_thread(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'chat-api.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    await storage.put_key("viewer-key", "viewer")
    await storage.put_key("operator-key", "operator")
    registry = IntegrationRegistry([], env_file=None)
    investigation = make_investigation_graph(
        UnavailableServices(), registry, checkpointer=InMemorySaver()
    )
    manager = RunManager(investigation, storage, RunContext)
    chat = make_chat_graph(
        ScriptedChatModel(responses=[AIMessage("Hola, ¿qué falla?"), AIMessage("Entendido.")]),
        registry,
        RunContext(),
        ThesisMemory(InMemoryStore()),
        policy=ApprovalPolicy(environment="prod"),
        checkpointer=InMemorySaver(),
    )
    app = create_app(manager=manager, registry=registry, chat_graph=chat)
    path = "/v1/chat/threads/thread-1/messages"

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        assert (await client.post(path, json={"message": "hola"})).status_code == 401
        assert (
            await client.post(path, json={"message": "hola"}, headers={"x-api-key": "viewer-key"})
        ).status_code == 403
        first = await client.post(
            path, json={"message": "hola"}, headers={"x-api-key": "operator-key"}
        )
        second = await client.post(
            path, json={"message": "sigue"}, headers={"x-api-key": "operator-key"}
        )
        assert first.status_code == 200
        assert first.headers["content-type"].startswith("text/event-stream")
        assert "event: message" in first.text
        assert "Hola" in first.text
        assert "Entendido" in second.text
    await engine.dispose()


async def test_chat_approval_requires_approver_and_rejects_without_writing(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'approve.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    await storage.put_key("operator-key", "operator")
    await storage.put_key("approver-key", "approver")
    registry = IntegrationRegistry([], env_file=None)
    investigation = make_investigation_graph(
        UnavailableServices(), registry, checkpointer=InMemorySaver()
    )
    manager = RunManager(investigation, storage, RunContext)
    chat = make_chat_graph(
        ScriptedChatModel(
            responses=[
                tool_call_message(
                    "propose_order",
                    {"ticker": "MSFT", "action": "buy", "quantity": 2, "reasoning": "tesis"},
                    "c1",
                ),
                AIMessage("Rechazo registrado."),
            ]
        ),
        registry,
        RunContext(),
        ThesisMemory(InMemoryStore()),
        policy=ApprovalPolicy(environment="prod"),
        checkpointer=InMemorySaver(),
    )
    notifications: list[tuple[str, str, str]] = []

    class Gateway:
        async def publish_approval(
            self, kind: str, thread_id: str, summary: str, approval_id: str
        ) -> str:
            notifications.append((kind, thread_id, summary))
            return "remote-1"

    app = create_app(manager=manager, registry=registry, chat_graph=chat, slack_gateway=Gateway())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        pending = await client.post(
            "/v1/chat/threads/t2/messages",
            json={"message": "Escala"},
            headers={"x-api-key": "operator-key"},
        )
        assert "event: approval" in pending.text
        assert len(notifications) == 1
        assert notifications[0][0:2] == ("chat", "t2")
        pending_state = await client.get(
            "/v1/chat/threads/t2", headers={"x-api-key": "operator-key"}
        )
        assert pending_state.status_code == 200
        assert pending_state.json()["status"] == "awaiting_approval"
        assert pending_state.json()["requests"][0]["action_requests"][0]["name"] == (
            "propose_order"
        )
        assert (
            await client.post(
                "/v1/chat/threads/t2/messages",
                json={"message": "Otra cosa"},
                headers={"x-api-key": "operator-key"},
            )
        ).status_code == 409
        path = "/v1/chat/threads/t2/approve"
        decision = {"decisions": [{"type": "reject"}]}
        assert (
            await client.post(path, json=decision, headers={"x-api-key": "operator-key"})
        ).status_code == 403
        assert await storage.approvals("t2") == []
        assert (
            await client.post(
                path,
                json={
                    "decisions": [
                        {
                            "type": "edit",
                            "edited_action": {"name": "save_note", "args": {"text": "unsafe"}},
                        }
                    ]
                },
                headers={"x-api-key": "approver-key"},
            )
        ).status_code == 422
        assert await storage.approvals("t2") == []
        rejected = await client.post(path, json=decision, headers={"x-api-key": "approver-key"})
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["status"] == "completed"
        completed_state = await client.get(
            "/v1/chat/threads/t2", headers={"x-api-key": "operator-key"}
        )
        assert completed_state.json()["status"] == "completed"
        approvals = await storage.approvals("t2")
        assert len(approvals) == 1
        assert approvals[0]["kind"] == "chat"
        assert approvals[0]["decision"] == "reject"
        assert approvals[0]["action_name"] == "propose_order"
        assert (
            approvals[0]["args_digest"]
            == hashlib.sha256(
                json.dumps(
                    {"ticker": "MSFT", "action": "buy", "quantity": 2, "reasoning": "tesis"},
                    sort_keys=True,
                ).encode()
            ).hexdigest()
        )
        assert approvals[0]["actor"] == hashlib.sha256(b"approver-key").hexdigest()
        assert (
            await client.post(path, json=decision, headers={"x-api-key": "approver-key"})
        ).status_code == 409
    await engine.dispose()
