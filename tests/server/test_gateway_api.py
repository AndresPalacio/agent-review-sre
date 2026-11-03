from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import Request
from fastapi.responses import Response
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from sqlalchemy.ext.asyncio import create_async_engine
from tests.server.finance_app import (
    analysis_manager,
    fixture_context,
    make_storage,
    paper_broker,
)

from fin_graph.domain.orders import OrderAction, ProposedOrder
from fin_graph.integrations.context import RunContext
from fin_graph.integrations.registry import IntegrationRegistry
from fin_graph.safety.approval import ApprovalPolicy
from fin_graph.server.app import _gateway_decide, create_app
from fin_graph.server.storage import Base, Storage
from fin_graph.trading.order_desk import OrderDesk
from fin_graph.workflows.orders.graph import make_order_graph


def _order(client_order_id: str = "gateway-order") -> ProposedOrder:
    return ProposedOrder(
        client_order_id=client_order_id,
        ticker="DEEP",
        action=OrderAction.BUY,
        quantity=2,
        reference_price=8.0,
        as_of=date(2024, 6, 28),
        reasoning="margen de seguridad",
    )


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
    slack, telegram = SlackStub(), TelegramStub()
    app = create_app(
        manager=analysis_manager(Storage(engine), fixture_context()),
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
    actor: str,
    first_via_api: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checks = iter([False, True])

    async def flaky(self: OrderDesk, execution: Any) -> bool:
        return next(checks)

    monkeypatch.setattr(OrderDesk, "verify", flaky)
    engine, storage = await make_storage(tmp_path / "gateway-rollback.sqlite")
    broker = paper_broker(engine)
    policy = ApprovalPolicy(environment="prod", allowed_approvers=frozenset({actor}))
    graph = make_order_graph(OrderDesk(broker), policy, checkpointer=InMemorySaver())
    app = create_app(
        manager=analysis_manager(storage, fixture_context(broker)),
        order_graph=graph,
        approval_policy=policy,
        paper=broker,
    )
    try:
        async with app.router.lifespan_context(app):
            first = await graph.ainvoke(
                {"order": _order().model_dump(mode="json")},
                {"configurable": {"thread_id": "orders:rollback-test"}},
            )
            first_id = first["__interrupt__"][0].id
            if first_via_api:
                await graph.ainvoke(
                    Command(resume={"decisions": [{"type": "approve"}]}),
                    {"configurable": {"thread_id": "orders:rollback-test"}},
                    context=RunContext(actor=actor, role="approver"),
                )
            else:
                await _gateway_decide(app, "orders", "rollback-test", "approve", actor, first_id)
            assert (await broker.portfolio()).position("DEEP").long == 2
            result = await _gateway_decide(
                app, "orders", "rollback-test", "approve", actor, first_id
            )
            assert "replaced" in result
            assert (await broker.portfolio()).position("DEEP").long == 2, (
                "the old button must not authorize the new rollback"
            )
            current = await graph.aget_state(
                {"configurable": {"thread_id": "orders:rollback-test"}}
            )
            await _gateway_decide(
                app,
                "orders",
                "rollback-test",
                "approve",
                actor,
                current.tasks[0].interrupts[0].id,
            )
            assert (await broker.portfolio()).position("DEEP").long == 0
    finally:
        await engine.dispose()


async def test_gateway_decision_uses_same_pending_checkpoint_and_audit(tmp_path: Path) -> None:
    engine, storage = await make_storage(tmp_path / "approval.sqlite")
    broker = paper_broker(engine)
    policy = ApprovalPolicy(environment="prod", allowed_approvers=frozenset({"slack:U1"}))
    orders = make_order_graph(OrderDesk(broker), policy, checkpointer=InMemorySaver())
    app = create_app(
        manager=analysis_manager(storage, fixture_context(broker)),
        order_graph=orders,
        approval_policy=policy,
        paper=broker,
    )
    async with app.router.lifespan_context(app):
        thread_id = "bot-approval"
        first = await orders.ainvoke(
            {"order": _order("reject-order").model_dump(mode="json")},
            {"configurable": {"thread_id": f"orders:{thread_id}"}},
        )
        assert "Rechazada" in await _gateway_decide(
            app, "orders", thread_id, "reject", "slack:U1", first["__interrupt__"][0].id
        )
        assert await broker.orders() == []
        assert (await storage.approvals(thread_id))[0]["actor"] == "slack:U1"
        assert (
            "already resolved"
            in (
                await _gateway_decide(
                    app,
                    "orders",
                    thread_id,
                    "approve",
                    "slack:U1",
                    first["__interrupt__"][0].id,
                )
            ).lower()
        )
    await engine.dispose()
