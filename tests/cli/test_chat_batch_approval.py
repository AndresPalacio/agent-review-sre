from __future__ import annotations

import json
from hashlib import sha256

import httpx
import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from fin_graph.agents.testing.scripted_model import ScriptedChatModel
from fin_graph.cli.chat import ChatSession, RemoteChatSession
from fin_graph.graphs.chat.graph import make_chat_graph
from fin_graph.graphs.remediation.graph import ActionCatalog
from fin_graph.integrations.context import RunContext
from fin_graph.integrations.registry import IntegrationRegistry
from fin_graph.memory.service import IncidentMemory
from fin_graph.persistence.action_ledger import open_sqlite_action_ledger
from fin_graph.safety.approval import ApprovalPolicy


def _local_session(store: InMemoryStore, *, note_count: int = 2, **kwargs) -> ChatSession:
    graph = make_chat_graph(
        ScriptedChatModel(
            responses=[
                AIMessage(
                    "",
                    tool_calls=[
                        {"name": "save_note", "args": {"text": "first note"}, "id": "note-1"},
                        {"name": "save_note", "args": {"text": "second note"}, "id": "note-2"},
                    ][:note_count],
                ),
                AIMessage("Decisiones procesadas"),
            ]
        ),
        IntegrationRegistry([], env_file=None),
        RunContext(),
        IncidentMemory(store),
        ActionCatalog([]),
        policy=ApprovalPolicy(environment="prod"),
        checkpointer=InMemorySaver(),
        store=store,
    )
    return ChatSession(graph, store, RunContext(), user_id="tester", **kwargs)


@pytest.mark.parametrize("choice", ["approve", "reject"])
async def test_local_approval_decides_and_audits_every_presented_action(choice, tmp_path) -> None:
    store = InMemoryStore()
    async with open_sqlite_action_ledger(tmp_path / "batch.sqlite") as audit:
        session = _local_session(store, approval_audit=audit)
        turn = await session.send("Save both notes")
        assert len(turn.requests[0]["action_requests"]) == 2

        reply = await session.command(f"/approve {choice}")

        assert reply == "Decisiones procesadas"
        records = await audit.approvals(session.thread_id)
        assert len(records) == 2
        assert [item["decision"] for item in records] == [choice, choice]
        assert {item["args_digest"] for item in records} == {
            sha256(b'{"text": "first note"}').hexdigest(),
            sha256(b'{"text": "second note"}').hexdigest(),
        }
        notes = await store.asearch(("org", "notes"))
        assert len(notes) == (2 if choice == "approve" else 0)
        state = await session.graph.aget_state(session._config())
        assert not any(task.interrupts for task in state.tasks)


@pytest.mark.parametrize("second_args", [{"text": "second edited note"}, {"wrong": "missing text"}])
async def test_local_batch_edit_is_refused_without_writes_audit_or_consuming_interrupt(
    second_args, tmp_path
) -> None:
    store = InMemoryStore()
    edited = {
        "edited_actions": [
            {"name": "save_note", "args": {"text": "first edited note"}},
            {"name": "save_note", "args": second_args},
        ]
    }
    async with open_sqlite_action_ledger(tmp_path / "edit-batch.sqlite") as audit:
        session = _local_session(store, approval_audit=audit)
        await session.send("Save both notes")
        error = None
        try:
            await session.command(f"/approve edit {json.dumps(edited)}")
        except ValueError as exc:
            error = exc

        assert await store.asearch(("org", "notes")) == []
        assert await audit.approvals(session.thread_id) == []
        state = await session.graph.aget_state(session._config())
        assert len(state.tasks[0].interrupts[0].value["action_requests"]) == 2
        assert error is not None and "only one pending action" in str(error)

        assert await session.command("/approve approve") == "Decisiones procesadas"
        notes = await store.asearch(("org", "notes"))
        assert {item.value["text"] for item in notes} == {"first note", "second note"}


@pytest.mark.parametrize(
    "edited",
    [
        {"name": "save_note", "args": {"text": "ambiguous"}},
        {"edited_actions": [{"name": "save_note", "args": {"text": "missing second"}}]},
        {
            "edited_actions": [
                {"name": "save_note", "args": {"text": "valid first"}},
                {"name": "execute_remediation", "args": {}},
            ]
        },
        {
            "edited_actions": [
                {"name": "save_note", "args": {"text": "valid first"}},
                {"name": "save_note", "args": []},
            ]
        },
    ],
)
async def test_invalid_local_batch_edit_keeps_all_actions_pending_without_audit(
    edited, tmp_path
) -> None:
    store = InMemoryStore()
    async with open_sqlite_action_ledger(tmp_path / "invalid.sqlite") as audit:
        session = _local_session(store, approval_audit=audit)
        await session.send("Save both notes")

        with pytest.raises(ValueError):
            await session.command(f"/approve edit {json.dumps(edited)}")

        assert await audit.approvals(session.thread_id) == []
        assert await store.asearch(("org", "notes")) == []
        state = await session.graph.aget_state(session._config())
        assert len(state.tasks[0].interrupts[0].value["action_requests"]) == 2


_REQUESTS = [
    {
        "action_requests": [
            {"name": "save_note", "arguments": {"text": "first note"}},
            {"name": "save_note", "arguments": {"text": "second note"}},
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
        transport=httpx.MockTransport(respond), base_url="https://sre.example"
    ) as client:
        session = RemoteChatSession(client, "secret-key", thread_id="batch")
        if source == "sse":
            assert (await session.send("Save notes")).status == "awaiting_approval"
        elif source == "resume":
            await session.command("/resume batch")

        assert await session.command(f"/approve {choice}") == "Both processed"


async def test_remote_batch_edit_is_refused_before_post_and_preserves_pending_batch() -> None:
    edited = [
        {"name": "save_note", "args": {"text": "first edited note"}},
        {"name": "save_note", "args": {"text": "second edited note"}},
    ]

    posted = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"status": "awaiting_approval", "requests": _REQUESTS})
        posted.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "completed", "reply": "Both rejected"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://sre.example"
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
        transport=httpx.MockTransport(respond), base_url="https://sre.example"
    ) as client:
        session = RemoteChatSession(client, "secret-key", thread_id="batch")
        with pytest.raises(ValueError, match="only one pending action"):
            await session.command('/approve edit {"name":"save_note","args":{"text":"one"}}')

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
                json={"status": "interrupted", "reply": "More notes", "requests": _REQUESTS},
            )
        return httpx.Response(200, json={"status": "completed", "reply": "Both rejected"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://sre.example"
    ) as client:
        session = RemoteChatSession(client, "secret-key", thread_id="batch")
        assert "second note" in await session.command("/approve approve")
        assert await session.command("/approve reject") == "Both rejected"

    assert choices == [[{"type": "approve"}], [{"type": "reject"}, {"type": "reject"}]]


async def test_local_single_action_edit_remains_compatible_and_audits_edited_arguments(
    tmp_path,
) -> None:
    store = InMemoryStore()
    async with open_sqlite_action_ledger(tmp_path / "single-edit.sqlite") as audit:
        session = _local_session(store, note_count=1, approval_audit=audit)
        await session.send("Save a note")

        reply = await session.command('/approve edit {"name":"save_note","args":{"text":"edited"}}')

        assert reply == "Decisiones procesadas"
        notes = await store.asearch(("org", "notes"))
        assert [item.value["text"] for item in notes] == ["edited"]
        records = await audit.approvals(session.thread_id)
        assert len(records) == 1
        assert records[0]["decision"] == "edit"
        assert records[0]["args_digest"] == sha256(b'{"text": "edited"}').hexdigest()


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
                {"type": "edit", "edited_action": {"name": "save_note", "args": {"text": "edited"}}}
            ]
        }:
            return httpx.Response(422)
        return httpx.Response(200, json={"status": "completed", "reply": "Edited"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://sre.example"
    ) as client:
        session = RemoteChatSession(client, "secret-key", thread_id="single")
        assert (
            await session.command('/approve edit {"name":"save_note","args":{"text":"edited"}}')
            == "Edited"
        )
