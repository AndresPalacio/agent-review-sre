from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from tests.server.finance_app import (
    REQUEST,
    analysis_manager,
    client_for,
    fixture_context,
    make_storage,
    order_graph,
    paper_broker,
)

from fin_graph.server.app import create_app
from fin_graph.trading.order_desk import OrderDesk
from fin_graph.workflows.orders.graph import PLACE_ORDER

ORDER = {"ticker": "deep", "action": "buy", "quantity": 10, "reasoning": "margen de seguridad"}
VIEWER = {"x-api-key": "viewer-key"}
OPERATOR = {"x-api-key": "operator-key"}
APPROVER = {"x-api-key": "approver-key"}


class Gateway:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, str]] = []

    async def publish_approval(
        self, kind: str, thread_id: str, summary: str, approval_id: str
    ) -> str:
        self.published.append((kind, thread_id, summary))
        return "remote-1"


async def _app(tmp_path: Path) -> tuple[Any, Any, Any, Any, Any, Gateway]:
    engine, storage = await make_storage(tmp_path / "orders.sqlite")
    broker = paper_broker(engine)
    manager = analysis_manager(storage, fixture_context(broker))
    gateway = Gateway()
    app = create_app(
        manager=manager, order_graph=order_graph(broker), paper=broker, slack_gateway=gateway
    )
    return app, engine, broker, storage, manager, gateway


async def test_an_order_waits_for_an_approver_and_a_rejection_never_fills(tmp_path: Path) -> None:
    app, engine, broker, storage, _, gateway = await _app(tmp_path)
    async with client_for(app) as client:
        assert (await client.post("/v1/orders", json=ORDER, headers=VIEWER)).status_code == 403
        queued = await client.post("/v1/orders", json=ORDER, headers=OPERATOR)
        assert queued.status_code == 202, queued.text
        body = queued.json()
        thread_id = body["thread_id"]
        assert body["status"] == "awaiting_approval"
        assert body["preview"]["allowed"] and body["preview"]["cash_after"] == 9_920.0
        assert "Comprar 10 DEEP" in body["requests"][0]["action_requests"][0]["description"]
        assert gateway.published[0][0:2] == ("orders", thread_id)
        pending = await client.get("/v1/orders/pending", headers=VIEWER)
        assert [item["thread_id"] for item in pending.json()] == [thread_id]
        path = f"/v1/orders/{thread_id}/approve"
        decision = {"decisions": [{"type": "reject"}], "approval_ids": body["approval_ids"]}
        assert (await client.post(path, json=decision, headers=OPERATOR)).status_code == 403
        rejected = await client.post(path, json=decision, headers=APPROVER)
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["outcome"] == "rejected"
        assert await broker.orders() == []
        [approval] = await storage.approvals(thread_id)
        assert (approval["kind"], approval["decision"]) == ("orders", "reject")
        assert approval["action_name"] == PLACE_ORDER
        assert approval["actor"] == hashlib.sha256(b"approver-key").hexdigest()
        assert (await client.post(path, json=decision, headers=APPROVER)).status_code == 409
    await engine.dispose()


async def test_an_approved_order_fills_and_shows_in_the_portfolio(tmp_path: Path) -> None:
    app, engine, _, _, _, _ = await _app(tmp_path)
    async with client_for(app) as client:
        queued = (await client.post("/v1/orders", json=ORDER, headers=OPERATOR)).json()
        edit = {"type": "edit", "edited_action": {"name": PLACE_ORDER, "args": {"quantity": 5}}}
        bad_edit = {"type": "edit", "edited_action": {"name": PLACE_ORDER, "args": {"ticker": "X"}}}
        path = f"/v1/orders/{queued['thread_id']}/approve"
        assert (
            await client.post(path, json={"decisions": [bad_edit]}, headers=APPROVER)
        ).status_code == 422
        filled = await client.post(path, json={"decisions": [edit]}, headers=APPROVER)
        assert filled.status_code == 200, filled.text
        assert filled.json()["outcome"] == "filled"
        assert filled.json()["execution"]["order"]["quantity"] == 5
        book = (await client.get("/v1/portfolio", headers=VIEWER)).json()
        orders = (await client.get("/v1/orders", headers=VIEWER)).json()

    assert book["paper"] is True and book["cash"] == 9_960.0
    assert book["positions"] == [
        {
            "ticker": "DEEP",
            "side": "long",
            "shares": 5,
            "cost_basis": 8.0,
            "price": 8.0,
            "market_value": 40.0,
            "unrealized_pnl": 0.0,
        }
    ]
    assert [item["status"] for item in orders] == ["filled"]
    await engine.dispose()


async def test_an_analysis_puts_its_proposed_orders_up_for_approval_once(tmp_path: Path) -> None:
    app, engine, _, _, manager, _ = await _app(tmp_path)
    async with client_for(app) as client:
        started = await client.post("/v1/analyses", json={"request": REQUEST}, headers=OPERATOR)
        await manager.wait(started.json()["run_id"])
        thread = started.json()["thread_id"]
        snapshot = await manager.graph.aget_state(manager._config(thread))
        proposed = snapshot.values.get("orders", [])
        first = await client.post(f"/v1/analyses/{thread}/orders", headers=OPERATOR)
        second = await client.post(f"/v1/analyses/{thread}/orders", headers=OPERATOR)

    assert first.status_code == 202, first.text
    assert len(first.json()) == len(proposed)
    assert [item["thread_id"] for item in first.json()] == [
        item["thread_id"] for item in second.json()
    ]
    await engine.dispose()


async def test_a_queued_order_can_be_cancelled_before_it_settles(tmp_path: Path) -> None:
    app, engine, broker, _, _, _ = await _app(tmp_path)
    broker.market_open = lambda now: False
    async with client_for(app) as client:
        queued = (await client.post("/v1/orders", json=ORDER, headers=OPERATOR)).json()
        approved = await client.post(
            f"/v1/orders/{queued['thread_id']}/approve",
            json={"decisions": [{"type": "approve"}]},
            headers=APPROVER,
        )
        assert approved.json()["outcome"] == "queued"
        order_id = approved.json()["execution"]["order"]["client_order_id"]
        assert (await client.post("/v1/orders/settle", headers=OPERATOR)).json() == []
        cancel_path = f"/v1/orders/{order_id}/cancel"
        assert (await client.post(cancel_path, headers=OPERATOR)).status_code == 403
        cancelled = await client.post(cancel_path, headers=APPROVER)
        assert cancelled.json()["status"] == "cancelled"
        assert (await client.post(cancel_path, headers=APPROVER)).json()["status"] == "cancelled"
    await engine.dispose()


async def test_a_fill_that_fails_verification_exposes_the_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def never(self: OrderDesk, execution: Any) -> bool:
        return False

    monkeypatch.setattr(OrderDesk, "verify", never)
    app, engine, _, _, _, _ = await _app(tmp_path)
    async with client_for(app) as client:
        queued = (await client.post("/v1/orders", json=ORDER, headers=OPERATOR)).json()
        approved = await client.post(
            f"/v1/orders/{queued['thread_id']}/approve",
            json={"decisions": [{"type": "approve"}]},
            headers=APPROVER,
        )

    body = approved.json()
    assert body["status"] == "awaiting_approval" and body["outcome"] == "rollback_pending"
    inverse = body["requests"][0]["action_requests"][0]["arguments"]
    assert (inverse["action"], inverse["quantity"]) == ("sell", 10)
    assert body["order"]["action"] == "sell"
    await engine.dispose()
