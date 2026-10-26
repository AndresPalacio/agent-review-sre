from __future__ import annotations

import json
from hashlib import sha256

import httpx
import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from tests.cli.paper_helpers import paper_context

from fin_graph.agents.testing.scripted_model import ScriptedChatModel
from fin_graph.cli.chat import ChatSession, RemoteChatSession
from fin_graph.graphs.chat.graph import make_chat_graph
from fin_graph.integrations.context import RunContext
from fin_graph.integrations.registry import IntegrationRegistry
from fin_graph.memory.theses import ThesisMemory
from fin_graph.persistence.action_ledger import open_sqlite_action_ledger
from fin_graph.safety.approval import ApprovalPolicy

ORDERS = [
    {"ticker": "MSFT", "action": "buy", "quantity": 1, "reasoning": "first"},
    {"ticker": "AAPL", "action": "buy", "quantity": 1, "reasoning": "second"},
]


def _local_session(
    store: InMemoryStore, context: RunContext, *, count: int = 2, **kwargs
) -> ChatSession:
    graph = make_chat_graph(
        ScriptedChatModel(
            responses=[
                AIMessage(
                    "",
                    tool_calls=[
                        {"name": "propose_order", "args": ORDERS[0], "id": "order-1"},
                        {"name": "propose_order", "args": ORDERS[1], "id": "order-2"},
                    ][:count],
                ),
                AIMessage("Decisiones procesadas"),
            ]
        ),
        IntegrationRegistry([], env_file=None),
        context,
        ThesisMemory(store),
        policy=ApprovalPolicy(environment="prod"),
        checkpointer=InMemorySaver(),
        store=store,
    )
    return ChatSession(graph, store, context, user_id="tester", **kwargs)


def _digest(args: dict) -> str:
    return sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()


@pytest.mark.parametrize("choice", ["approve", "reject"])
async def test_local_approval_decides_and_audits_every_presented_action(choice, tmp_path) -> None:
    store = InMemoryStore()
    async with (
        open_sqlite_action_ledger(tmp_path / "batch.sqlite") as audit,
        paper_context(tmp_path / "paper.sqlite") as (broker, context),
    ):
        session = _local_session(store, context, approval_audit=audit)
        turn = await session.send("Compra MSFT y AAPL")
        assert len(turn.requests[0]["action_requests"]) == 2

        reply = await session.command(f"/approve {choice}")

        assert reply == "Decisiones procesadas"
        records = await audit.approvals(session.thread_id)
        assert [item["decision"] for item in records] == [choice, choice]
        assert {item["args_digest"] for item in records} == {_digest(item) for item in ORDERS}
        assert len(await broker.orders()) == (2 if choice == "approve" else 0)
        state = await session.graph.aget_state(session._config())
        assert not any(task.interrupts for task in state.tasks)


@pytest.mark.parametrize("second_args", [{"quantity": 2}, {"wrong": "missing order"}])
async def test_local_batch_edit_is_refused_without_writes_audit_or_consuming_interrupt(
    second_args, tmp_path
) -> None:
    store = InMemoryStore()
    edited = {
        "edited_actions": [
            {"name": "propose_order", "args": ORDERS[0] | {"quantity": 3}},
            {"name": "propose_order", "args": second_args},
        ]
    }
    async with (
        open_sqlite_action_ledger(tmp_path / "edit-batch.sqlite") as audit,
        paper_context(tmp_path / "paper.sqlite") as (broker, context),
    ):
        session = _local_session(store, context, approval_audit=audit)
        await session.send("Compra MSFT y AAPL")
        with pytest.raises(ValueError, match="only one pending action"):
            await session.command(f"/approve edit {json.dumps(edited)}")

        assert await broker.orders() == []
        assert await audit.approvals(session.thread_id) == []
        state = await session.graph.aget_state(session._config())
        assert len(state.tasks[0].interrupts[0].value["action_requests"]) == 2

        assert await session.command("/approve approve") == "Decisiones procesadas"
        assert {item.order.ticker for item in await broker.orders()} == {"MSFT", "AAPL"}


@pytest.mark.parametrize(
    "edited",
    [
        {"name": "propose_order", "args": ORDERS[0]},
        {"edited_actions": [{"name": "propose_order", "args": ORDERS[0]}]},
        {
            "edited_actions": [
                {"name": "propose_order", "args": ORDERS[0]},
                {"name": "cancel_paper_order", "args": {}},
            ]
        },
        {
            "edited_actions": [
                {"name": "propose_order", "args": ORDERS[0]},
                {"name": "propose_order", "args": []},
            ]
        },
    ],
)
async def test_invalid_local_batch_edit_keeps_all_actions_pending_without_audit(
    edited, tmp_path
) -> None:
    store = InMemoryStore()
    async with (
        open_sqlite_action_ledger(tmp_path / "invalid.sqlite") as audit,
        paper_context(tmp_path / "paper.sqlite") as (broker, context),
    ):
        session = _local_session(store, context, approval_audit=audit)
        await session.send("Compra MSFT y AAPL")

        with pytest.raises(ValueError):
            await session.command(f"/approve edit {json.dumps(edited)}")

        assert await audit.approvals(session.thread_id) == []
        assert await broker.orders() == []
        state = await session.graph.aget_state(session._config())
        assert len(state.tasks[0].interrupts[0].value["action_requests"]) == 2


async def test_local_single_action_edit_remains_compatible_and_audits_edited_arguments(
    tmp_path,
) -> None:
    store = InMemoryStore()
    edited_args = ORDERS[0] | {"quantity": 3}
    async with (
        open_sqlite_action_ledger(tmp_path / "single.sqlite") as audit,
        paper_context(tmp_path / "paper.sqlite") as (broker, context),
    ):
        session = _local_session(store, context, count=1, approval_audit=audit)
        await session.send("Compra MSFT")
        edit = {"name": "propose_order", "args": edited_args}

        assert await session.command(f"/approve edit {json.dumps(edit)}") == (
            "Decisiones procesadas"
        )

        [record] = await audit.approvals(session.thread_id)
        assert record["decision"] == "edit" and record["args_digest"] == _digest(edited_args)
        [order] = await broker.orders()
        assert order.order.quantity == 3


_REQUESTS = [
    {
        "action_requests": [
            {
                "name": "propose_order",
                "arguments": {"ticker": "MSFT", "action": "buy", "quantity": 1},
            },
            {
                "name": "propose_order",
                "arguments": {"ticker": "AAPL", "action": "buy", "quantity": 1},
            },
        ]
    }
]


@pytest.mark.parametrize("choice", ["approve", "reject"])
@pytest.mark.parametrize("source", ["sse", "resume", "existing_thread"])
async def test_remote_approval_decides_every_presented_action(choice, source) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"status": "awaiting_approval", "requests": _REQUESTS})
        if request.url.path.endswith("/messages"):
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=f"event: approval\ndata: {json.dumps({'requests': _REQUESTS})}\n\n",
            )
        if json.loads(request.content) != {"decisions": [{"type": choice}, {"type": choice}]}:
            return httpx.Response(422, json={"detail": "decision count does not match"})
        return httpx.Response(200, json={"status": "completed", "reply": "Both processed"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://fin.example"
    ) as client:
        session = RemoteChatSession(client, "secret-key", thread_id="batch")
        if source == "sse":
            assert (await session.send("Compra")).status == "awaiting_approval"
        elif source == "resume":
            await session.command("/resume batch")

        assert await session.command(f"/approve {choice}") == "Both processed"


async def test_remote_batch_edit_is_refused_before_post_and_preserves_pending_batch() -> None:
    edited = [
        {"name": "propose_order", "args": {"ticker": "MSFT", "action": "buy", "quantity": 2}},
        {"name": "propose_order", "args": {"ticker": "AAPL", "action": "buy", "quantity": 2}},
    ]

    posted = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"status": "awaiting_approval", "requests": _REQUESTS})
        posted.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "completed", "reply": "Both rejected"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://fin.example"
    ) as client:
        session = RemoteChatSession(client, "secret-key", thread_id="batch")
        argument = json.dumps({"edited_actions": edited})
        with pytest.raises(ValueError, match="only one pending action"):
            await session.command(f"/approve edit {argument}")
        assert posted == []
        assert await session.command("/approve reject") == "Both rejected"

    assert posted == [{"decisions": [{"type": "reject"}, {"type": "reject"}]}]


async def test_remote_batch_edit_rejects_ambiguous_single_action() -> None:
    posted = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"status": "awaiting_approval", "requests": _REQUESTS})
        posted.append(request)
        return httpx.Response(200, json={"status": "completed", "reply": "Unexpected edit"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://fin.example"
    ) as client:
        session = RemoteChatSession(client, "secret-key", thread_id="batch")
        with pytest.raises(ValueError, match="only one pending action"):
            await session.command('/approve edit {"name":"propose_order","args":{"text":"one"}}')

    assert posted == []


async def test_remote_approval_uses_the_followup_batch_after_another_interrupt() -> None:
    choices = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "status": "awaiting_approval",
                    "requests": [{"action_requests": [_REQUESTS[0]["action_requests"][0]]}],
                },
            )
        decisions = json.loads(request.content)["decisions"]
        choices.append(decisions)
        if len(choices) == 1:
            return httpx.Response(
                200,
                json={"status": "interrupted", "reply": "More orders", "requests": _REQUESTS},
            )
        return httpx.Response(200, json={"status": "completed", "reply": "Both rejected"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://fin.example"
    ) as client:
        session = RemoteChatSession(client, "secret-key", thread_id="batch")
        assert "AAPL" in await session.command("/approve approve")
        assert await session.command("/approve reject") == "Both rejected"

    assert choices == [[{"type": "approve"}], [{"type": "reject"}, {"type": "reject"}]]


async def test_remote_single_action_edit_remains_compatible() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "status": "awaiting_approval",
                    "requests": [{"action_requests": [_REQUESTS[0]["action_requests"][0]]}],
                },
            )
        if json.loads(request.content) != {
            "decisions": [
                {
                    "type": "edit",
                    "edited_action": {"name": "propose_order", "args": {"text": "edited"}},
                }
            ]
        }:
            return httpx.Response(422)
        return httpx.Response(200, json={"status": "completed", "reply": "Edited"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://fin.example"
    ) as client:
        session = RemoteChatSession(client, "secret-key", thread_id="single")
        assert (
            await session.command('/approve edit {"name":"propose_order","args":{"text":"edited"}}')
            == "Edited"
        )
