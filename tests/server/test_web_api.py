from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import create_async_engine

from fin_graph.graphs.investigation.graph import make_investigation_graph
from fin_graph.graphs.investigation.services import UnavailableServices
from fin_graph.graphs.remediation.graph import ActionCatalog, ActionSpec, make_remediation_graph
from fin_graph.integrations.context import RunContext
from fin_graph.integrations.registry import IntegrationRegistry
from fin_graph.safety.approval import ApprovalPolicy
from fin_graph.server.app import create_app
from fin_graph.server.manager import RunManager
from fin_graph.server.storage import Base
from fin_graph.server.storage import Storage as DatabaseStorage


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
                "diagnosis": {},
                "verification": {},
                "actions": [],
                "errors": [],
                "result": {"password": "private"},
                "rollback": "password=private",
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
                            id="current", value={"action_requests": [{"name": "scale", "args": {}}]}
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
    app = create_app(manager=Manager(), chat_graph=Graph(), remediation_graph=Graph())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        assert (await client.get("/v1/session")).status_code == 401
        assert (await client.get("/v1/session", headers={"x-api-key": "viewer"})).json() == {
            "role": "viewer"
        }
        detail = await client.get("/v1/investigations/t", headers={"x-api-key": "viewer"})
        assert (
            set(("plan", "findings", "evidence", "diagnosis", "verification", "actions", "errors"))
            <= detail.json().keys()
        )
        assert "private" not in detail.text
        chat = (await client.get("/v1/chat/threads/t", headers={"x-api-key": "operator"})).json()
        assert chat["approval_ids"] == ["current"]
        assert [m["role"] for m in chat["messages"]] == ["user", "assistant"]
        assert "private" not in str(chat["messages"])
        remediation = await client.get("/v1/remediations/t", headers={"x-api-key": "viewer"})
        assert "result" in remediation.json() and "rollback" in remediation.json()
        assert "private" not in remediation.text
        assert (
            await client.get("/v1/remediations/actions", headers={"x-api-key": "viewer"})
        ).status_code == 403
        catalog = await client.get("/v1/remediations/actions", headers={"x-api-key": "operator"})
        assert catalog.status_code == 200 and "schema" in catalog.json()[0]
        for route in ("/v1/chat/threads/t/approve", "/v1/remediations/t/approve"):
            response = await client.post(
                route,
                headers={"x-api-key": "approver"},
                json={"decisions": [{"type": "approve"}], "approval_ids": ["old"]},
            )
            assert response.status_code == 409


async def test_rollback_replaces_approval_ids_and_catalog_is_injected(tmp_path: Path) -> None:
    class Args(BaseModel):
        replicas: int

    writes: list[int] = []

    async def prepare(args: Args, context: RunContext) -> dict[str, Any]:
        return {"replicas": args.replicas}

    async def execute(args: Args, key: str, context: RunContext) -> dict[str, Any]:
        writes.append(args.replicas)
        return {"replicas": args.replicas}

    async def verify(args: Args, context: RunContext) -> bool:
        return args.replicas == 1

    def rollback(
        args: Args, prepared: dict[str, Any], result: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        return "scale", {"replicas": 1}

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'web.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = DatabaseStorage(engine)
    await storage.put_key("operator", "operator")
    await storage.put_key("approver", "approver")
    registry = IntegrationRegistry([], env_file=None)
    manager = RunManager(
        make_investigation_graph(UnavailableServices(), registry, checkpointer=InMemorySaver()),
        storage,
        RunContext,
    )
    catalog = ActionCatalog(
        [ActionSpec("scale", Args, prepare, execute, verify, rollback_action=rollback)]
    )
    app = create_app(
        manager=manager,
        action_catalog=catalog,
        remediation_graph=make_remediation_graph(
            catalog, ApprovalPolicy(environment="prod"), checkpointer=InMemorySaver()
        ),
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        actions = (
            await client.get("/v1/remediations/actions", headers={"x-api-key": "operator"})
        ).json()
        assert [a["name"] for a in actions] == ["scale"]
        queued = (
            await client.post(
                "/v1/remediations",
                headers={"x-api-key": "operator"},
                json={"tool": "scale", "args": {"replicas": 2}},
            )
        ).json()
        assert writes == [] and len(queued["approval_ids"]) == 1
        path = f"/v1/remediations/{queued['thread_id']}/approve"
        stale = await client.post(
            path,
            headers={"x-api-key": "approver"},
            json={"decisions": [{"type": "approve"}], "approval_ids": ["stale"]},
        )
        assert stale.status_code == 409
        assert writes == [] and await storage.approvals(queued["thread_id"]) == []
        payload = {"decisions": [{"type": "approve"}], "approval_ids": queued["approval_ids"]}
        approved = await client.post(path, headers={"x-api-key": "approver"}, json=payload)
        assert approved.status_code == 200
        assert approved.json()["approval_ids"] != queued["approval_ids"]
        assert writes == [2]
        replay = await client.post(path, headers={"x-api-key": "approver"}, json=payload)
        assert replay.status_code == 409 and writes == [2]
        assert len(await storage.approvals(queued["thread_id"])) == 1
        malformed = await client.post(
            path,
            headers={"x-api-key": "approver"},
            json={"decisions": [{"type": "approve"}], "approval_ids": "bad"},
        )
        assert malformed.status_code == 422 and writes == [2]
        payload["approval_ids"] = approved.json()["approval_ids"]
        current = await client.post(path, headers={"x-api-key": "approver"}, json=payload)
        assert current.status_code == 200 and current.json()["approval_ids"] == []
        assert writes == [2, 1]
        assert len(await storage.approvals(queued["thread_id"])) == 2
    await engine.dispose()
