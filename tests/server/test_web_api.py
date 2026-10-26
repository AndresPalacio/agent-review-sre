from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from tests.server.finance_app import (
    analysis_manager,
    client_for,
    fixture_context,
    make_storage,
    order_graph,
    paper_broker,
)
from tests.server.test_orders_api import OPERATOR, ORDER

from fin_graph.graphs.orders.desk import OrderDesk
from fin_graph.server.app import create_app


class Storage:
    async def close(self) -> None:
        pass

    async def key_role(self, key: str) -> str | None:
        return key if key in {"viewer", "operator", "approver"} else None

    async def latest_run(self, thread: str) -> dict[str, Any]:
        return {"status": "completed", "error": "password=private"}

    @asynccontextmanager
    async def approval_lock(self, *args: Any):
        yield


class Graph:
    async def aget_state(self, config: Any) -> Any:
        return SimpleNamespace(
            values={
                "plan": {"token": "private"},
                "findings": [],
                "evidence": [],
                "theses": [],
                "verification": {},
                "limits": {},
                "decisions": {},
                "orders": [],
                "degraded": [],
                "errors": [],
                "order": {"ticker": "DEEP", "password": "private"},
                "preview": {"allowed": True, "password": "private"},
                "execution": {"outcome": "filled", "password": "private"},
                "messages": [
                    HumanMessage("hello"),
                    ToolMessage("private", tool_call_id="t"),
                    AIMessage("secret tool", tool_calls=[{"id": "t", "name": "x", "args": {}}]),
                    AIMessage("password=private"),
                ],
            },
            tasks=[
                SimpleNamespace(
                    interrupts=[
                        SimpleNamespace(
                            id="current",
                            value={"action_requests": [{"name": "place_paper_order", "args": {}}]},
                        )
                    ]
                )
            ],
        )


class Manager:
    storage = Storage()
    graph = Graph()

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    def _config(self, thread: str) -> dict[str, Any]:
        return {}


async def test_web_contracts_and_stale_approvals() -> None:
    app = create_app(manager=Manager(), chat_graph=Graph(), order_graph=Graph())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        assert (await client.get("/v1/session")).status_code == 401
        assert (await client.get("/v1/session", headers={"x-api-key": "viewer"})).json() == {
            "role": "viewer"
        }
        detail = await client.get("/v1/analyses/t", headers={"x-api-key": "viewer"})
        assert (
            set(
                (
                    "plan",
                    "findings",
                    "evidence",
                    "theses",
                    "verification",
                    "limits",
                    "decisions",
                    "orders",
                    "errors",
                )
            )
            <= detail.json().keys()
        )
        assert "private" not in detail.text
        chat = (await client.get("/v1/chat/threads/t", headers={"x-api-key": "operator"})).json()
        assert chat["approval_ids"] == ["current"]
        assert [m["role"] for m in chat["messages"]] == ["user", "assistant"]
        assert "private" not in str(chat["messages"])
        order = await client.get("/v1/orders/t", headers={"x-api-key": "viewer"})
        assert "preview" in order.json() and "execution" in order.json()
        assert "private" not in order.text
        for route in ("/v1/chat/threads/t/approve", "/v1/orders/t/approve"):
            response = await client.post(
                route,
                headers={"x-api-key": "approver"},
                json={"decisions": [{"type": "approve"}], "approval_ids": ["old"]},
            )
            assert response.status_code == 409


async def test_rollback_replaces_approval_ids(tmp_path: Path, monkeypatch: Any) -> None:
    checks = iter([False, True])

    async def flaky(self: OrderDesk, execution: Any) -> bool:
        return next(checks)

    monkeypatch.setattr(OrderDesk, "verify", flaky)
    engine, storage = await make_storage(tmp_path / "web.sqlite")
    broker = paper_broker(engine)
    manager = analysis_manager(storage, fixture_context(broker))
    app = create_app(
        manager=manager,
        order_graph=order_graph(broker),
        paper=broker,
    )
    async with client_for(app) as client:
        queued = (
            await client.post(
                "/v1/orders",
                headers=OPERATOR,
                json=ORDER,
            )
        ).json()
        assert await broker.orders() == [] and len(queued["approval_ids"]) == 1
        path = f"/v1/orders/{queued['thread_id']}/approve"
        stale = await client.post(
            path,
            headers={"x-api-key": "approver-key"},
            json={"decisions": [{"type": "approve"}], "approval_ids": ["stale"]},
        )
        assert stale.status_code == 409
        assert await broker.orders() == [] and await storage.approvals(queued["thread_id"]) == []
        payload = {"decisions": [{"type": "approve"}], "approval_ids": queued["approval_ids"]}
        approved = await client.post(path, headers={"x-api-key": "approver-key"}, json=payload)
        assert approved.status_code == 200
        assert approved.json()["approval_ids"] != queued["approval_ids"]
        assert (await broker.portfolio()).position("DEEP").long == 10
        replay = await client.post(path, headers={"x-api-key": "approver-key"}, json=payload)
        assert replay.status_code == 409
        assert (await broker.portfolio()).position("DEEP").long == 10
        assert len(await storage.approvals(queued["thread_id"])) == 1
        malformed = await client.post(
            path,
            headers={"x-api-key": "approver-key"},
            json={"decisions": [{"type": "approve"}], "approval_ids": "bad"},
        )
        assert malformed.status_code == 422
        assert (await broker.portfolio()).position("DEEP").long == 10
        payload["approval_ids"] = approved.json()["approval_ids"]
        current = await client.post(path, headers={"x-api-key": "approver-key"}, json=payload)
        assert current.status_code == 200 and current.json()["approval_ids"] == []
        assert (await broker.portfolio()).position("DEEP").long == 0
        assert len(await storage.approvals(queued["thread_id"])) == 2
    await engine.dispose()
