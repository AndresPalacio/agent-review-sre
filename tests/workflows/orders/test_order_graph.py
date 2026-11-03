from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from fin_graph.domain.fills import OrderStatus
from fin_graph.domain.orders import OrderAction, ProposedOrder
from fin_graph.domain.portfolio import Portfolio
from fin_graph.integrations.broker.paper.broker import PaperBroker
from fin_graph.integrations.context import RunContext
from fin_graph.persistence.paper_ledger import open_sqlite_paper_ledger
from fin_graph.safety.approval import ApprovalPolicy
from fin_graph.safety.trading_limits import TradingLimits
from fin_graph.trading.order_desk import OrderDesk
from fin_graph.workflows.orders.graph import PLACE_ORDER, make_order_graph

APPROVER = RunContext(actor="trader-1", role="approver")
ORDER = ProposedOrder(
    client_order_id="ord_graph",
    ticker="MSFT",
    action=OrderAction.BUY,
    quantity=10,
    reference_price=400.0,
    as_of=date(2024, 6, 28),
    reasoning="verified quality compounder",
)


class Quotes:
    async def __call__(self, ticker: str) -> float | None:
        return 400.0


@pytest.fixture
async def paper(tmp_path: Path) -> AsyncIterator[PaperBroker]:
    async with open_sqlite_paper_ledger(tmp_path / "paper.sqlite") as store:
        yield PaperBroker(
            store=store,
            quotes=Quotes(),
            limits=TradingLimits(max_order_notional=20_000, max_position_pct=1.0),
            initial=Portfolio(cash=50_000.0),
            clock=lambda: datetime(2024, 6, 28, 14, 0, tzinfo=UTC),
            market_open=lambda now: True,
        )


def graph(paper: PaperBroker, policy: ApprovalPolicy | None = None) -> Any:
    return make_order_graph(
        OrderDesk(paper), policy or ApprovalPolicy(environment="prod"), checkpointer=InMemorySaver()
    )


def config(thread: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread}}


async def start(workflow: Any, thread: str, order: ProposedOrder = ORDER) -> dict[str, Any]:
    return dict(await workflow.ainvoke({"order": order.model_dump(mode="json")}, config(thread)))


async def decide(workflow: Any, thread: str, decision: dict[str, Any], context: RunContext) -> Any:
    return await workflow.ainvoke(
        Command(resume={"decisions": [decision]}), config(thread), context=context
    )


async def test_an_order_waits_for_approval_then_fills_and_verifies(paper: PaperBroker) -> None:
    workflow = graph(paper)

    paused = await start(workflow, "approve")
    request = paused["__interrupt__"][0].value
    assert request["action_requests"][0]["name"] == PLACE_ORDER
    assert "Comprar 10 MSFT" in request["action_requests"][0]["description"]
    assert "no se mueve dinero real" in request["action_requests"][0]["description"]
    assert await paper.orders() == []

    done = await decide(workflow, "approve", {"type": "approve"}, APPROVER)

    assert done["outcome"] == "filled" and done["verified"] and done["approver"] == "trader-1"
    assert (await paper.portfolio()).position("MSFT").long == 10


async def test_rejection_and_unauthorized_approval_never_reach_the_broker(
    paper: PaperBroker,
) -> None:
    workflow = graph(paper)
    await start(workflow, "reject")

    refused = await decide(workflow, "reject", {"type": "approve"}, RunContext(actor="bot"))
    assert "approver is required" in refused["denial"]
    assert "approver is required" in refused["__interrupt__"][0].value["denial"]
    expiry = RunContext(actor="scheduler:expiry", role="internal_rejector")
    refused = await decide(workflow, "reject", {"type": "approve"}, expiry)
    assert "may only reject" in refused["denial"]
    # The refused approvals are never replayed under the next decider's identity.
    done = await decide(workflow, "reject", {"type": "reject"}, APPROVER)

    assert done["outcome"] == "rejected" and done["approver"] == "trader-1"
    assert await paper.orders() == []


async def test_an_edit_resizes_the_order_under_a_new_id(paper: PaperBroker) -> None:
    workflow = graph(paper)
    await start(workflow, "edit")
    edit = {"type": "edit", "edited_action": {"name": PLACE_ORDER, "args": {"quantity": 4}}}

    done = await decide(workflow, "edit", edit, APPROVER)

    assert done["outcome"] == "filled"
    assert done["order"]["quantity"] == 4 and done["order"]["client_order_id"] != "ord_graph"
    assert (await paper.portfolio()).position("MSFT").long == 4
    await start(workflow, "bad-edit")
    refused = await decide(
        workflow,
        "bad-edit",
        {"type": "edit", "edited_action": {"name": PLACE_ORDER, "args": {"ticker": "TSLA"}}},
        APPROVER,
    )
    assert "integer quantity" in refused["denial"] and "__interrupt__" in refused
    assert (await paper.portfolio()).position("TSLA").long == 0


async def test_an_order_the_limits_forbid_is_never_put_to_a_human(paper: PaperBroker) -> None:
    workflow = graph(paper)
    oversized = ORDER.model_copy(update={"client_order_id": "ord_big", "quantity": 100})

    done = await start(workflow, "blocked", oversized)

    assert done["outcome"] == "blocked" and "__interrupt__" not in done
    assert any("max_order_notional" in item for item in done["preview"]["violations"])


async def test_dev_auto_approval_and_closed_market_queue_the_order(paper: PaperBroker) -> None:
    paper.market_open = lambda now: False
    policy = ApprovalPolicy(environment="dev", auto_approve=frozenset({PLACE_ORDER}))

    done = await start(graph(paper, policy), "auto")

    assert done["outcome"] == "queued"
    [queued] = await paper.orders()
    assert queued.status is OrderStatus.QUEUED


async def test_a_fill_that_fails_verification_proposes_its_inverse(
    paper: PaperBroker, monkeypatch: pytest.MonkeyPatch
) -> None:
    checks = iter([False, True])

    async def flaky(self: OrderDesk, execution: Any) -> bool:
        return next(checks)

    monkeypatch.setattr(OrderDesk, "verify", flaky)
    workflow = graph(paper)
    await start(workflow, "rollback")

    paused = await decide(workflow, "rollback", {"type": "approve"}, APPROVER)
    inverse = paused["__interrupt__"][0].value["action_requests"][0]["arguments"]
    assert inverse["action"] == "sell" and inverse["quantity"] == 10
    done = await decide(workflow, "rollback", {"type": "approve"}, APPROVER)

    assert done["outcome"] == "rolled_back"
    assert (await paper.portfolio()).position("MSFT").long == 0
