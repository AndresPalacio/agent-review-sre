from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import Request
from fastapi.responses import Response
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import create_async_engine

from sre_graph.graphs.investigation.graph import make_investigation_graph
from sre_graph.graphs.investigation.services import UnavailableServices
from sre_graph.graphs.remediation.graph import ActionCatalog, ActionSpec, make_remediation_graph
from sre_graph.integrations.context import RunContext
from sre_graph.integrations.registry import IntegrationRegistry
from sre_graph.safety.approval import ApprovalPolicy
from sre_graph.server.app import _gateway_decide, create_app
from sre_graph.server.manager import RunManager
from sre_graph.server.storage import Base, Storage


class SlackStub:
    def __init__(self) -> None:
        self.calls = 0

    async def handle(self, request: Request) -> Response:
        self.calls += 1
        return Response(status_code=200)


class TelegramStub:
    def __init__(self) -> None:
        self.updates: list[tuple[dict[str, Any], str | None]] = []

    async def handle(self, update: dict[str, Any], secret: str | None) -> int:
        self.updates.append((update, secret))
        return 200 if secret == "expected" else 401


async def test_gateway_routes_are_optional_and_delegate_verified_payloads(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gateway.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    registry = IntegrationRegistry([], env_file=None)
    graph = make_investigation_graph(UnavailableServices(), registry, checkpointer=InMemorySaver())
    slack, telegram = SlackStub(), TelegramStub()
    app = create_app(
        manager=RunManager(graph, Storage(engine), RunContext),
        registry=registry,
        slack_gateway=slack,
        telegram_gateway=telegram,
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        assert (await client.post("/v1/gateway/slack", content=b"signed-event")).status_code == 200
        assert slack.calls == 1
        assert (await client.post("/v1/gateway/telegram", json={"update_id": 1})).status_code == 401
        response = await client.post(
            "/v1/gateway/telegram",
            json={"update_id": 2},
            headers={"x-telegram-bot-api-secret-token": "expected"},
        )
        assert response.status_code == 200
        assert telegram.updates[-1] == ({"update_id": 2}, "expected")
    await engine.dispose()


@pytest.mark.parametrize("actor", ["slack:U1", "telegram:U1"])
@pytest.mark.parametrize("first_via_api", [False, True])
async def test_old_gateway_approval_cannot_authorize_new_rollback(
    actor: str, first_via_api: bool
) -> None:
    class Args(BaseModel):
        replicas: int

    writes: list[int] = []

    async def prepare(args: Args, context: RunContext) -> dict[str, int]:
        return {"previous_replicas": 1}

    async def execute(args: Args, key: str, context: RunContext) -> dict[str, int]:
        writes.append(args.replicas)
        return {"replicas": args.replicas}

    async def verify(args: Args, context: RunContext) -> bool:
        return args.replicas == 1

    def rollback(
        args: Args, prepared: dict[str, Any], result: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        return "scale", {"replicas": prepared["previous_replicas"]}

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    registry = IntegrationRegistry([], env_file=None)
    policy = ApprovalPolicy(environment="prod", allowed_approvers=frozenset({actor}))
    graph = make_remediation_graph(
        ActionCatalog(
            [ActionSpec("scale", Args, prepare, execute, verify, rollback_action=rollback)]
        ),
        policy,
        checkpointer=InMemorySaver(),
    )
    app = create_app(
        manager=RunManager(
            make_investigation_graph(UnavailableServices(), registry), storage, RunContext
        ),
        registry=registry,
        remediation_graph=graph,
        approval_policy=policy,
    )
    try:
        async with app.router.lifespan_context(app):
            first = await graph.ainvoke(
                {"tool": "scale", "args": {"replicas": 2}, "action_id": "first"},
                {"configurable": {"thread_id": "remediation:rollback-test"}},
            )
            first_id = first["__interrupt__"][0].id
            if first_via_api:
                await graph.ainvoke(
                    Command(resume={"decisions": [{"type": "approve"}]}),
                    {"configurable": {"thread_id": "remediation:rollback-test"}},
                    context=RunContext(actor=actor, role="approver"),
                )
            else:
                await _gateway_decide(
                    app, "remediation", "rollback-test", "approve", actor, first_id
                )
            assert writes == [2]
            result = await _gateway_decide(
                app, "remediation", "rollback-test", "approve", actor, first_id
            )
            assert "replaced" in result
            assert writes == [2], "the old button must not authorize the new rollback"
            current = await graph.aget_state(
                {"configurable": {"thread_id": "remediation:rollback-test"}}
            )
            await _gateway_decide(
                app,
                "remediation",
                "rollback-test",
                "approve",
                actor,
                current.tasks[0].interrupts[0].id,
            )
            assert writes == [2, 1]
    finally:
        await engine.dispose()


async def test_gateway_decision_uses_same_pending_checkpoint_and_audit(tmp_path: Path) -> None:
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

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'approval.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    registry = IntegrationRegistry([], env_file=None)
    investigation = make_investigation_graph(
        UnavailableServices(), registry, checkpointer=InMemorySaver()
    )
    policy = ApprovalPolicy(environment="prod", allowed_approvers=frozenset({"slack:U1"}))
    remediation = make_remediation_graph(
        ActionCatalog([ActionSpec("scale", Args, prepare, execute, verify)]),
        policy,
        checkpointer=InMemorySaver(),
    )
    app = create_app(
        manager=RunManager(investigation, storage, RunContext),
        registry=registry,
        remediation_graph=remediation,
        approval_policy=policy,
    )
    async with app.router.lifespan_context(app):
        thread_id = "bot-approval"
        first = await remediation.ainvoke(
            {"tool": "scale", "args": {"replicas": 2}, "action_id": thread_id},
            {"configurable": {"thread_id": f"remediation:{thread_id}"}},
        )
        assert "Rejected" in await _gateway_decide(
            app, "remediation", thread_id, "reject", "slack:U1", first["__interrupt__"][0].id
        )
        assert writes == []
        assert (await storage.approvals(thread_id))[0]["actor"] == "slack:U1"
        assert (
            "already resolved"
            in (
                await _gateway_decide(
                    app,
                    "remediation",
                    thread_id,
                    "approve",
                    "slack:U1",
                    first["__interrupt__"][0].id,
                )
            ).lower()
        )
    await engine.dispose()
