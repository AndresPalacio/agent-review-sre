from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import create_async_engine

from sre_graph.graphs.investigation.graph import make_investigation_graph
from sre_graph.graphs.investigation.services import UnavailableServices
from sre_graph.graphs.remediation.graph import ActionCatalog, ActionSpec, make_remediation_graph
from sre_graph.integrations.context import RunContext
from sre_graph.integrations.registry import IntegrationRegistry
from sre_graph.safety.approval import ApprovalPolicy
from sre_graph.server.app import create_app
from sre_graph.server.manager import RunManager
from sre_graph.server.storage import Base, Storage


async def test_remediation_api_requires_approval_and_preserves_rejection(tmp_path: Path) -> None:
    class Args(BaseModel):
        replicas: int

    writes: list[int] = []

    async def prepare(args: Args, context: RunContext) -> dict[str, int]:
        return {"replicas": args.replicas}

    async def execute(args: Args, key: str, context: RunContext) -> dict[str, int]:
        writes.append(args.replicas)
        return {"replicas": args.replicas}

    async def verify(args: Args, context: RunContext) -> bool:
        return True

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'remediation.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    await storage.put_key("operator-key", "operator")
    await storage.put_key("approver-key", "approver")
    registry = IntegrationRegistry([], env_file=None)
    manager = RunManager(
        make_investigation_graph(UnavailableServices(), registry, checkpointer=InMemorySaver()),
        storage,
        RunContext,
    )
    remediation = make_remediation_graph(
        ActionCatalog([ActionSpec("scale", Args, prepare, execute, verify)]),
        ApprovalPolicy(environment="prod"),
        checkpointer=InMemorySaver(),
    )
    notifications: list[tuple[str, str, str]] = []

    class Gateway:
        async def publish_approval(
            self, kind: str, thread_id: str, summary: str, approval_id: str
        ) -> str:
            notifications.append((kind, thread_id, summary))
            return "remote-1"

    app = create_app(
        manager=manager,
        registry=registry,
        remediation_graph=remediation,
        slack_gateway=Gateway(),
    )

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        queued = await client.post(
            "/v1/remediations",
            json={"tool": "scale", "args": {"replicas": 2}},
            headers={"x-api-key": "operator-key"},
        )
        assert queued.status_code == 202, queued.text
        assert queued.json()["status"] == "awaiting_approval"
        assert writes == []
        thread_id = queued.json()["thread_id"]
        assert len(notifications) == 1
        assert notifications[0][0:2] == ("remediation", thread_id)
        assert "scale" in notifications[0][2]
        path = f"/v1/remediations/{thread_id}/approve"
        assert (
            await client.post(
                path,
                json={"decisions": [{"type": "approve"}]},
                headers={"x-api-key": "operator-key"},
            )
        ).status_code == 403
        assert await storage.approvals(thread_id) == []
        rejected = await client.post(
            path,
            json={"decisions": [{"type": "reject"}]},
            headers={"x-api-key": "approver-key"},
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["outcome"] == "rejected"
        assert writes == []
        approvals = await storage.approvals(thread_id)
        assert len(approvals) == 1
        assert approvals[0]["kind"] == "remediation"
        assert approvals[0]["decision"] == "reject"
        assert approvals[0]["action_name"] == "scale"
        assert (
            approvals[0]["args_digest"]
            == hashlib.sha256(json.dumps({"replicas": 2}, sort_keys=True).encode()).hexdigest()
        )
        assert approvals[0]["actor"] == hashlib.sha256(b"approver-key").hexdigest()
        assert (
            await client.post(
                path,
                json={"decisions": [{"type": "approve"}]},
                headers={"x-api-key": "approver-key"},
            )
        ).status_code == 409
    await engine.dispose()


async def test_failed_verification_exposes_next_rollback_approval(tmp_path: Path) -> None:
    class Args(BaseModel):
        replicas: int

    writes: list[int] = []

    async def prepare(args: Args, context: RunContext) -> dict[str, int]:
        return {"replicas": args.replicas}

    async def execute(args: Args, key: str, context: RunContext) -> dict[str, int]:
        writes.append(args.replicas)
        return {"replicas": args.replicas}

    async def verify(args: Args, context: RunContext) -> bool:
        return args.replicas == 1

    def rollback(
        args: Args, prepared: dict[str, int], result: dict[str, int]
    ) -> tuple[str, dict[str, int]]:
        return "scale", {"replicas": 1}

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rollback.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    await storage.put_key("operator-key", "operator")
    await storage.put_key("approver-key", "approver")
    registry = IntegrationRegistry([], env_file=None)
    manager = RunManager(
        make_investigation_graph(UnavailableServices(), registry, checkpointer=InMemorySaver()),
        storage,
        RunContext,
    )
    remediation = make_remediation_graph(
        ActionCatalog(
            [ActionSpec("scale", Args, prepare, execute, verify, rollback_action=rollback)]
        ),
        ApprovalPolicy(environment="prod"),
        checkpointer=InMemorySaver(),
    )
    app = create_app(manager=manager, registry=registry, remediation_graph=remediation)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        queued = await client.post(
            "/v1/remediations",
            json={"tool": "scale", "args": {"replicas": 2}},
            headers={"x-api-key": "operator-key"},
        )
        thread_id = queued.json()["thread_id"]
        approved = await client.post(
            f"/v1/remediations/{thread_id}/approve",
            json={"decisions": [{"type": "approve"}]},
            headers={"x-api-key": "approver-key"},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "awaiting_approval"
        assert approved.json()["outcome"] == "rollback_pending"
        assert approved.json()["requests"][0]["action_requests"][0]["arguments"] == {"replicas": 1}
        assert writes == [2]
    await engine.dispose()
