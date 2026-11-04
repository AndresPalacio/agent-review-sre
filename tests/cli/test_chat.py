from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from pytest import MonkeyPatch
from tests.cli.paper_helpers import paper_context
from typer.testing import CliRunner

from fin_graph.agents.chat.supervisor import make_chat_graph
from fin_graph.agents.common.testing.scripted_model import ScriptedChatModel, tool_call_message
from fin_graph.cli.chat import ChatSession, RemoteChatSession
from fin_graph.cli.main import app
from fin_graph.integrations.context import RunContext
from fin_graph.integrations.registry import IntegrationRegistry
from fin_graph.memory.theses import ThesisMemory
from fin_graph.persistence.action_ledger import open_sqlite_action_ledger
from fin_graph.safety.approval import ApprovalPolicy


async def test_local_session_new_resume_threads_and_cost_without_transcript_in_store() -> None:
    store = InMemoryStore()
    registry = IntegrationRegistry([], env_file=None)
    graph = make_chat_graph(
        ScriptedChatModel(responses=[AIMessage("Hola, ¿qué analizamos?")]),
        registry,
        RunContext(),
        ThesisMemory(store),
        policy=ApprovalPolicy(environment="prod"),
        checkpointer=InMemorySaver(),
        store=store,
    )
    session = ChatSession(graph, store, RunContext(), registry=registry, user_id="tester")

    first_id = await session.start()
    turn = await session.send("hola")
    assert turn.text == "Hola, ¿qué analizamos?"
    assert "0 tokens" in await session.command("/cost")
    second_id = await session.command("/new")
    assert second_id != first_id
    assert first_id in await session.command("/threads")
    assert await session.command(f"/resume {first_id}") == first_id
    prefs = await store.aget(("users", "tester", "prefs"), "threads")
    assert prefs is not None
    assert "analizamos" not in str(prefs.value)


async def test_local_approval_has_durable_actor_audit(tmp_path) -> None:
    store = InMemoryStore()
    order = {"ticker": "MSFT", "action": "buy", "quantity": 1, "reasoning": "tesis"}
    async with (
        open_sqlite_action_ledger(tmp_path / "chat.sqlite") as audit,
        paper_context(tmp_path / "paper.sqlite") as (broker, context),
    ):
        graph = make_chat_graph(
            ScriptedChatModel(
                responses=[
                    tool_call_message("propose_order", order, "order-1"),
                    AIMessage("Orden ejecutada"),
                ]
            ),
            IntegrationRegistry([], env_file=None),
            context,
            ThesisMemory(store),
            policy=ApprovalPolicy(environment="prod"),
            checkpointer=InMemorySaver(),
            store=store,
        )
        session = ChatSession(graph, store, context, user_id="tester", approval_audit=audit)
        await session.start()
        assert (await session.send("Compra 1 MSFT")).status == "awaiting_approval"
        await session.command("/approve approve")
        records = await audit.approvals(session.thread_id or "")
        assert len(await broker.orders()) == 1
    assert records[0]["actor"] == "local:tester"
    assert records[0]["decision"] == "approve"
    assert records[0]["action_name"] == "propose_order"


async def test_local_analysis_command_renders_the_report() -> None:
    from tests.domain.test_analysis_report import _report

    class Analysis:
        async def ainvoke(self, value: dict, **kwargs: object) -> dict:
            assert value["raw_request"] == "DEEP DIST"
            return {"report": _report().model_dump(mode="json"), "is_noise": False}

    session = ChatSession(None, InMemoryStore(), RunContext(), analysis=Analysis())

    rendered = await session.command("/analiza DEEP DIST")

    assert "Análisis DEEP" in rendered
    with pytest.raises(ValueError):
        await session.command("/analiza")


def test_local_approval_request_redacts_nested_credentials() -> None:
    session = ChatSession(None, InMemoryStore(), RunContext())
    turn = session._turn(
        {
            "messages": [AIMessage("Acción preparada")],
            "__interrupt__": [
                SimpleNamespace(value={"action_requests": [{"args": {"api_key": "secret-key"}}]})
            ],
        }
    )
    assert turn.status == "awaiting_approval"
    assert turn.requests == [
        {"action_requests": [{"args": {"api_key": "[REDACTED:assigned_secret]"}}]}
    ]


def test_cli_exposes_ask_and_chat_commands() -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["ask", "--help"]).exit_code == 0
    chat_help = runner.invoke(app, ["chat", "--help"])
    assert chat_help.exit_code == 0
    assert "--sqlite-path" in chat_help.output


async def test_remote_chat_consumes_sse_and_posts_approval_with_api_key() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/messages"):
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=(
                    "event: message\n"
                    'data: {"text":"Preparando secret-key","thread_id":"thread-1"}\n\n'
                    "event: approval\n"
                    'data: {"requests":[{"action_requests":[{"name":"scale"}]}]}\n\n'
                ),
            )
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "thread_id": "thread-1",
                "reply": "Aprobado secret-key",
                "requests": [],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://finance.example"
    ) as client:
        session = RemoteChatSession(client, "secret-key", thread_id="thread-1")
        assert await session.start() == "thread-1"
        turn = await session.send("Escala")
        assert turn.text == "Preparando [REDACTED:api_key]"
        assert turn.status == "awaiting_approval"
        assert turn.requests == [{"action_requests": [{"name": "scale"}]}]
        assert await session.command("/approve approve") == "Aprobado [REDACTED:api_key]"

    assert [item.url.path for item in requests] == [
        "/v1/chat/threads/thread-1/messages",
        "/v1/chat/threads/thread-1/approve",
    ]
    assert all(item.headers["x-api-key"] == "secret-key" for item in requests)
    assert requests[0].content == b'{"message":"Escala"}'
    assert requests[1].content == b'{"decisions":[{"type":"approve"}]}'


async def test_remote_chat_handles_thread_commands_and_redacts_http_errors() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"status": "completed", "requests": []})
        return httpx.Response(401, json={"detail": "secret-key invalid"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://finance.example"
    ) as client:
        session = RemoteChatSession(client, "secret-key")
        first = await session.start()
        second = await session.command("/new")
        assert second != first
        assert first in await session.command("/threads")
        assert await session.command(f"/resume {first}") == first
        assert await session.command("/resume existing-server-thread") == "existing-server-thread"
        try:
            await session.send("hola")
        except ValueError as exc:
            assert "401" in str(exc)
            assert "secret-key" not in str(exc)
        else:
            raise AssertionError("HTTP error should fail the turn")


async def test_remote_resume_recovers_pending_approval_without_sse_history() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(
            200,
            json={
                "status": "awaiting_approval",
                "requests": [{"action_requests": [{"name": "scale", "args": {"replicas": 2}}]}],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://finance.example"
    ) as client:
        session = RemoteChatSession(client, "secret-key")
        output = await session.command("/resume existing-thread")
    assert "Aprobación pendiente" in output
    assert '"replicas": 2' in output


async def test_remote_approval_shows_a_followup_rollback_request() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "status": "awaiting_approval",
                    "requests": [
                        {
                            "action_requests": [
                                {
                                    "name": "propose_order",
                                    "arguments": {"ticker": "MSFT", "quantity": 3},
                                }
                            ]
                        }
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "status": "interrupted",
                "reply": "",
                "requests": [
                    {
                        "action_requests": [
                            {
                                "name": "propose_order",
                                "arguments": {"ticker": "MSFT", "quantity": 1},
                            }
                        ]
                    }
                ],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://finance.example"
    ) as client:
        session = RemoteChatSession(client, "secret-key", thread_id="thread-1")
        output = await session.command("/approve approve")
    assert "Aprobación pendiente" in output
    assert '"ticker": "MSFT"' in output
    assert '"quantity": 1' in output


async def test_remote_transport_exception_hides_api_key() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret-key connection details", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(fail), base_url="https://finance.example"
    ) as client:
        session = RemoteChatSession(client, "secret-key", thread_id="t1")
        try:
            await session.send("hola")
        except ValueError as exc:
            assert "ConnectError" in str(exc)
            assert "secret-key" not in str(exc)
        else:
            raise AssertionError("transport error should fail the turn")


async def test_remote_analysis_polls_for_report_and_reads_integrations() -> None:
    from tests.domain.test_analysis_report import _report

    report = _report()
    paths: list[str] = []
    checks = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal checks
        paths.append(request.url.path)
        assert request.headers["x-api-key"] == "secret-key"
        if request.url.path == "/v1/integrations":
            return httpx.Response(200, json=[{"id": "financial_datasets", "status": "available"}])
        if request.method == "POST":
            assert request.content == b'{"text":"DEEP"}'
            return httpx.Response(
                202, json={"run_id": "run-1", "thread_id": "analysis-1", "status": "queued"}
            )
        checks += 1
        if checks == 1:
            return httpx.Response(200, json={"run": {"status": "running"}, "report": None})
        return httpx.Response(
            200,
            json={"run": {"status": "completed"}, "report": report.model_dump(mode="json")},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://finance.example"
    ) as client:
        session = RemoteChatSession(client, "secret-key")
        assert await session.command("/integrations") == "financial_datasets: available"
        result = await session.command("/analiza DEEP")

    assert "Análisis DEEP" in result
    assert paths == [
        "/v1/integrations",
        "/v1/analyses",
        "/v1/analyses/analysis-1",
        "/v1/analyses/analysis-1",
    ]


def test_cli_remote_options_are_available() -> None:
    runner = CliRunner()
    for command in ("ask", "chat"):
        help_result = runner.invoke(app, [command, "--help"])
        assert help_result.exit_code == 0
        assert "--server" in help_result.output
        assert "--api-key" in help_result.output
        assert "--thread-id" in help_result.output


def test_ask_remote_uses_env_api_key_and_existing_thread(monkeypatch: MonkeyPatch) -> None:
    paths: list[str] = []
    keys: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        keys.append(request.headers["x-api-key"])
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text='event: message\ndata: {"text":"Respuesta remota"}\n\n'
            'event: done\ndata: {"thread_id":"saved-thread"}\n\n',
        )

    real_client = httpx.AsyncClient

    def mock_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        return real_client(*args, **kwargs, transport=httpx.MockTransport(respond))

    monkeypatch.setattr("fin_graph.cli.main.httpx.AsyncClient", mock_client)
    result = CliRunner().invoke(
        app,
        ["ask", "hola", "--server", "https://finance.example", "--thread-id", "saved-thread"],
        env={"FIN_GRAPH_API_KEY": "secret-key"},
    )
    assert result.exit_code == 0, result.output
    assert "Respuesta remota" in result.output
    assert "Hilo saved-thread" in result.output
    assert "secret-key" not in result.output
    assert paths == ["/v1/chat/threads/saved-thread/messages"]
    assert keys == ["secret-key"]


def test_remote_api_key_requires_tls_outside_loopback() -> None:
    result = CliRunner().invoke(
        app, ["ask", "hola", "--server", "http://finance.example", "--api-key", "secret-key"]
    )
    assert result.exit_code != 0
    assert "HTTPS" in result.output
    assert "secret-key" not in result.output
