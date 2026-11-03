"""Pilot runtime contracts, with real graphs and SDK over an HTTP boundary double."""

from __future__ import annotations

import asyncio
import importlib
import json
import time
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph_sdk.client import LangGraphClient

from fin_graph.agents.common.testing.scripted_model import ScriptedChatModel
from fin_graph.work import graphs
from fin_graph.work.store import WorkStore

_LEDGER: WorkStore | None = None


@pytest.fixture(autouse=True)
def control_ledger(tmp_path: Path):
    global _LEDGER
    _LEDGER = WorkStore(tmp_path / "control.sqlite")
    yield
    _LEDGER = None


def context(**changes: Any) -> dict[str, Any]:
    ctx = {
        "actor": "local-tester",
        "work_id": "work-fixture",
        "revision": 0,
        "max_children": 2,
        "deadline": time.time() + 60,
        "delay_seconds": 0,
        **changes,
    }
    if "ledger_path" not in ctx and _LEDGER is not None:
        # Like the gateway: the ledger holds the revision this run was dispatched with.
        _LEDGER.save(
            {
                "work_id": ctx["work_id"],
                "actor": ctx["actor"],
                "revision": ctx["revision"],
                "cancel_requested": False,
            }
        )
        ctx["ledger_path"] = str(_LEDGER.path)
    return ctx


class AgentProtocolServer:
    """HTTP boundary only: graph logic, tools, middleware and SDK stay real."""

    def __init__(self) -> None:
        self.threads: dict[str, dict[str, Any]] = {}
        self.runs: dict[str, dict[str, Any]] = {}
        self.launches: list[dict[str, Any]] = []
        self.cancellations: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        parts = request.url.path.strip("/").split("/")
        body = json.loads(request.content) if request.content else {}
        if parts == ["threads"] and request.method == "POST":
            thread_id = str(uuid5(NAMESPACE_URL, f"fixture-thread-{len(self.threads)}"))
            self.threads[thread_id] = {
                "thread_id": thread_id,
                "created_at": "2026-10-22T00:00:00Z",
                "updated_at": "2026-10-22T00:00:00Z",
                "metadata": body.get("metadata", {}),
                "status": "idle",
                "config": {},
                "values": {},
                "interrupts": {},
            }
            return httpx.Response(200, json=self.threads[thread_id])
        if len(parts) == 2 and request.method == "GET":
            return httpx.Response(200, json=self.threads[parts[1]])
        if len(parts) == 3 and parts[2] == "runs" and request.method == "POST":
            run_id = str(uuid5(NAMESPACE_URL, f"fixture-run-{len(self.runs)}"))
            self.launches.append({"thread_id": parts[1], **body})
            self.runs[run_id] = {
                "run_id": run_id,
                "thread_id": parts[1],
                "assistant_id": body["assistant_id"],
                "created_at": "2026-10-22T00:00:00Z",
                "updated_at": "2026-10-22T00:00:00Z",
                "status": "running",
                "metadata": body.get("metadata", {}),
                "kwargs": body,
                "multitask_strategy": body.get("multitask_strategy", "reject"),
            }
            self.threads[parts[1]]["status"] = "busy"
            return httpx.Response(200, json=self.runs[run_id])
        if len(parts) == 4 and request.method == "GET":
            return httpx.Response(200, json=self.runs[parts[3]])
        if len(parts) == 5 and parts[-1] == "cancel":
            self.cancellations.append(parts[3])
            self.runs[parts[3]]["status"] = "interrupted"
            return httpx.Response(204)
        raise AssertionError(f"Unexpected Agent Protocol request: {request.method} {request.url}")


@pytest.fixture
async def server(monkeypatch: pytest.MonkeyPatch):
    import deepagents.middleware.async_subagents as upstream

    boundary = AgentProtocolServer()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(boundary.handle), base_url="http://agent.test"
    ) as http:
        client = LangGraphClient(http)
        monkeypatch.setattr(upstream, "get_client", lambda **_: client)
        monkeypatch.setattr(graphs, "get_client", lambda **_: client)
        monkeypatch.setenv("FIN_ASYNC_MODEL", "offline")
        yield boundary


async def test_offline_supervisor_launches_real_async_tools_and_portable_context(server):
    ctx = context()
    result = await (await graphs.supervisor({})).ainvoke(
        {"messages": [HumanMessage("Analiza fundamentales y técnicos ticker=DEMO horizon=medium")]},
        config={
            "configurable": {"thread_id": "parent"},
            "metadata": {"assistant_id": "supervisor"},
        },
        context=ctx,
    )
    assert {task["agent_name"] for task in result["async_tasks"].values()} == {
        "fundamentals",
        "technicals",
    }
    assert len(result["async_tasks"]) == 2
    assert [launch["context"] for launch in server.launches] == [ctx, ctx]
    assert all("config" not in launch for launch in server.launches)
    assert all(t["metadata"]["parent_thread_id"] == "parent" for t in server.threads.values())
    assert {row["tool"] for row in result["decision_trace"]} == {"start_async_task"}
    assert all(row["provenance"] == "offline" for row in result["decision_trace"])
    assert "offline" in result["messages"][-1].content.lower()


async def test_parallel_model_calls_cannot_exceed_two_children(server, monkeypatch):
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "start_async_task",
                        "args": {"subagent_type": "fundamentals", "description": "read"},
                        "id": f"c{i}",
                    }
                    for i in range(5)
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    monkeypatch.setattr(graphs, "_model", lambda role: model)
    result = await (await graphs.supervisor({})).ainvoke(
        {"messages": [HumanMessage("go")]}, context=context(max_children=20)
    )
    assert len(result["async_tasks"]) == 2
    errors = [m for m in result["messages"] if isinstance(m, ToolMessage) and m.status == "error"]
    assert len(errors) == 3
    assert all("child_limit" in m.content for m in errors)


@pytest.mark.parametrize(
    "ctx_changes,agent,reason",
    [
        ({"deadline": 1}, "fundamentals", "deadline"),
        ({}, "shell", "agent_type"),
        (None, "fundamentals", "context"),
    ],
)
async def test_start_gate_rejects_invalid_runtime_without_launch(
    server, monkeypatch, ctx_changes, agent, reason
):
    ctx = context(**ctx_changes) if ctx_changes is not None else {}
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "start_async_task",
                        "args": {"subagent_type": agent, "description": "read"},
                        "id": "s",
                    }
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    monkeypatch.setattr(graphs, "_model", lambda role: model)
    result = await (await graphs.supervisor({})).ainvoke(
        {"messages": [HumanMessage("go")]}, context=ctx
    )
    assert not server.launches
    assert any(reason in m.content for m in result["messages"] if isinstance(m, ToolMessage))


@pytest.mark.parametrize("name", ["update_async_task", "cancel_async_task"])
async def test_mutation_cannot_target_an_unregistered_child(server, monkeypatch, name):
    args = {"task_id": "someone-elses-thread"}
    if name == "update_async_task":
        args["message"] = "continue"
    model = ScriptedChatModel(
        responses=[
            AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": "s"}]),
            AIMessage(content="done"),
        ]
    )
    monkeypatch.setattr(graphs, "_model", lambda role: model)
    result = await (await graphs.supervisor({})).ainvoke(
        {"messages": [HumanMessage("go")]}, context=context()
    )
    assert not server.launches and not server.cancellations
    assert any(
        "unregistered" in m.content for m in result["messages"] if isinstance(m, ToolMessage)
    )


async def test_trace_redacts_credentials_without_recording_model_reasoning(server, monkeypatch):
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="private reasoning must not be traced",
                tool_calls=[
                    {
                        "name": "start_async_task",
                        "args": {
                            "subagent_type": "fundamentals",
                            "description": "read token=very-private-value",
                        },
                        "id": "s",
                    }
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    monkeypatch.setattr(graphs, "_model", lambda role: model)
    result = await (await graphs.supervisor({})).ainvoke(
        {"messages": [HumanMessage("go")]}, context=context()
    )
    trace = json.dumps(result["decision_trace"])
    assert "very-private-value" not in trace
    assert "private reasoning" not in trace
    assert "REDACTED" in trace


@pytest.mark.parametrize("role", ["fundamentals", "technicals"])
async def test_child_returns_explicit_fixture_and_honors_latest_request(server, role):
    graph = await getattr(graphs, role)({})
    result = await graph.ainvoke(
        {"messages": [HumanMessage("ticker=DEMO horizon=short")]}, context=context(revision=2)
    )
    observation = json.loads(result["messages"][-1].content)
    assert observation["synthetic"] is True
    assert observation["source"] == "synthetic_fixture"
    assert observation["ticker"] == "DEMO"
    assert observation["horizon"] == "short"
    assert observation["revision"] == 2
    assert observation["kind"] == role
    later = await graph.ainvoke(
        {"messages": [*result["messages"], HumanMessage("ticker=PAGO horizon=long")]},
        context=context(revision=3),
    )
    assert json.loads(later["messages"][-1].content)["ticker"] == "PAGO"


async def test_worker_denies_deepagent_builtin_write_tool(server, monkeypatch):
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"file_path": "/should-not-exist", "content": "unsafe"},
                        "id": "w",
                    }
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    monkeypatch.setattr(graphs, "_model", lambda role: model)
    result = await (await graphs.fundamentals({})).ainvoke(
        {"messages": [HumanMessage("go")]}, context=context()
    )
    assert not result.get("files")
    assert any(
        "tool_not_allowed" in m.content for m in result["messages"] if isinstance(m, ToolMessage)
    )


async def test_offline_update_targets_existing_technicals_and_preserves_ticker(server):
    graph = await graphs.supervisor({})
    initial = await graph.ainvoke(
        {"messages": [HumanMessage("Analiza fundamentales y técnicos ticker=DEMO horizon=medium")]},
        context=context(),
    )
    result = await graph.ainvoke(
        {
            **initial,
            "messages": [
                *initial["messages"],
                HumanMessage("Actualiza el hijo de técnicos para horizonte=short"),
            ],
        },
        context=context(revision=1),
    )
    assert len(result["async_tasks"]) == 2
    assert result["work_revision"] == 1
    updated = server.launches[-1]
    assert updated["assistant_id"] == "technicals"
    assert updated["multitask_strategy"] == "interrupt"
    assert updated["context"]["revision"] == 1
    worker = await graphs.technicals({})
    first = await worker.ainvoke(
        {"messages": [HumanMessage("ticker=DEMO horizon=medium")]}, context=context()
    )
    second = await worker.ainvoke(
        {"messages": [*first["messages"], HumanMessage("Revisa horizonte=short")]},
        context=context(revision=1),
    )
    observation = json.loads(second["messages"][-1].content)
    assert observation["ticker"] == "DEMO"
    assert observation["horizon"] == "short"


@pytest.mark.parametrize("cached_status", ["waiting", "running"])
async def test_update_cannot_discard_cached_or_new_approval(server, monkeypatch, cached_status):
    initial = await (await graphs.supervisor({})).ainvoke(
        {"messages": [HumanMessage("Analiza técnicos")]}, context=context()
    )
    task_id, tracked = next(iter(initial["async_tasks"].items()))
    tracked["status"] = cached_status
    server.threads[task_id]["status"] = "interrupted"
    server.threads[task_id]["interrupts"] = {"pause": [{"id": "approval", "value": "approve?"}]}
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "update_async_task",
                        "args": {"task_id": task_id, "message": "continue"},
                        "id": "u",
                    }
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    monkeypatch.setattr(graphs, "_model", lambda role: model)
    result = await (await graphs.supervisor({})).ainvoke(
        {**initial, "messages": [*initial["messages"], HumanMessage("update")]}, context=context()
    )
    assert len(server.launches) == 1
    assert any(
        "waiting_for_approval" in m.content
        for m in result["messages"]
        if isinstance(m, ToolMessage)
    )


async def test_offline_cancel_and_status_use_tracked_tasks_after_deadline(server):
    graph = await graphs.supervisor({})
    initial = await graph.ainvoke(
        {"messages": [HumanMessage("Analiza técnicos")]}, context=context()
    )
    task_id = next(iter(initial["async_tasks"]))
    status = await graph.ainvoke(
        {**initial, "messages": [*initial["messages"], HumanMessage("estado")]}, context=context()
    )
    assert task_id in status["messages"][-1].content
    cancelled = await graph.ainvoke(
        {**status, "messages": [*status["messages"], HumanMessage("cancela técnicos")]},
        context=context(deadline=1),
    )
    assert cancelled["async_tasks"][task_id]["status"] == "cancelled"
    assert len(server.cancellations) == 1


async def test_deepagent_child_notifies_parent_with_real_native_protocol(server):
    parent_id = "parent-thread"
    server.threads[parent_id] = {
        "thread_id": parent_id,
        "status": "idle",
        "metadata": {},
        "interrupts": {},
    }
    supervisor = await graphs.supervisor({})
    initial = await supervisor.ainvoke(
        {"messages": [HumanMessage("Analiza fundamentales")]},
        config={
            "configurable": {"thread_id": parent_id},
            "metadata": {"assistant_id": "supervisor"},
        },
        context=context(),
    )
    task = next(iter(initial["async_tasks"].values()))
    await (await graphs.fundamentals({})).ainvoke(
        {"messages": [HumanMessage("ticker=DEMO horizon=short")]},
        config={
            "configurable": {"thread_id": task["thread_id"]},
            "metadata": {
                **server.threads[task["thread_id"]]["metadata"],
                "run_id": task["run_id"],
            },
        },
        context=context(),
    )
    notification_run = server.launches[-1]
    assert notification_run["thread_id"] == parent_id
    notification = notification_run["input"]["messages"][0]
    assert notification["deepagents_notification"]["status"] == "success"
    received = await supervisor.ainvoke(
        {**initial, "messages": [*initial["messages"], notification]}, context=context()
    )
    assert received["async_tasks"][task["task_id"]]["status"] == "success"
    assert len(server.launches) == 2
    assert "synthetic" in received["messages"][-1].content


@pytest.mark.parametrize(
    "role,limit,tool_name,args",
    [
        ("supervisor", 20, "list_async_tasks", {}),
        ("fundamentals", 6, "read_fundamentals_fixture", {"ticker": "FIXT", "horizon": "short"}),
    ],
)
async def test_run_budget_stops_repeated_model_calls(
    server, monkeypatch, role, limit, tool_name, args
):
    model = ScriptedChatModel(
        responses=[
            AIMessage(content="", tool_calls=[{"name": tool_name, "args": args, "id": f"loop-{i}"}])
            for i in range(30)
        ]
    )
    monkeypatch.setattr(graphs, "_model", lambda role: model)
    result = await (await getattr(graphs, role)({})).ainvoke(
        {"messages": [HumanMessage("go")]}, context=context(), config={"recursion_limit": 200}
    )
    assert len(model.calls) == limit
    assert "limit" in result["messages"][-1].content.lower()
    assert result["result_revision"] == 0


async def test_child_delay_remains_cancellable(server):
    task = asyncio.create_task(
        (await graphs.fundamentals({})).ainvoke(
            {"messages": [HumanMessage("ticker=FIXT horizon=short")]},
            context=context(delay_seconds=5),
        )
    )
    await asyncio.sleep(0.1)
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_direct_mode_answers_without_spawning_or_claiming_real_inference(server):
    result = await (await graphs.supervisor({})).ainvoke(
        {"messages": [HumanMessage("¿Qué significa P/E?")]}, context=context(max_children=0)
    )
    assert not server.launches
    assert not result.get("decision_trace")
    assert result["model_mode"] == "offline"
    assert "offline" in result["messages"][-1].content.lower()
    assert "p/e" in result["messages"][-1].content.lower()


@pytest.mark.parametrize(
    "control,role,reason",
    [
        (
            {"actor": "another-user", "revision": 1, "cancel_requested": False},
            "supervisor",
            "actor",
        ),
        (
            {"actor": "local-tester", "revision": 1, "cancel_requested": False},
            "supervisor",
            "stale_revision",
        ),
        (
            {"actor": "local-tester", "revision": 0, "cancel_requested": True},
            "fundamentals",
            "work_cancelled",
        ),
    ],
)
async def test_ledger_gate_blocks_foreign_stale_and_cancelled_work(
    server, tmp_path, control, role, reason
):
    from fin_graph.work.store import WorkStore

    ledger = WorkStore(tmp_path / "work.sqlite")
    ledger.save({"work_id": "work-fixture", **control})
    result = await (await getattr(graphs, role)({})).ainvoke(
        {"messages": [HumanMessage("Analiza fundamentales")]},
        context=context(ledger_path=str(ledger.path)),
    )
    assert not server.launches
    assert any(reason in m.content for m in result["messages"] if isinstance(m, ToolMessage))


async def test_ledger_failure_is_a_redacted_denial(server, tmp_path):
    result = await (await graphs.supervisor({})).ainvoke(
        {"messages": [HumanMessage("Analiza fundamentales")]},
        context=context(ledger_path=str(tmp_path / "missing.sqlite")),
    )
    assert not server.launches
    assert "control_unavailable" in result["messages"][-1].content
    assert str(tmp_path) not in result["messages"][-1].content


@pytest.mark.parametrize("role", ["supervisor", "fundamentals"])
async def test_missing_ledger_path_fails_closed(server, role):
    ctx = {key: value for key, value in context().items() if key != "ledger_path"}
    result = await (await getattr(graphs, role)({})).ainvoke(
        {"messages": [HumanMessage("ticker=DEMO horizon=short")]}, context=ctx
    )
    assert not server.launches
    denials = [m.content for m in result["messages"] if isinstance(m, ToolMessage)]
    assert denials and all("control_unavailable" in content for content in denials)


async def test_worker_may_read_old_revision_but_state_revision_cannot_regress(server, tmp_path):
    from fin_graph.work.store import WorkStore

    ledger = WorkStore(tmp_path / "work.sqlite")
    ledger.save(
        {
            "work_id": "work-fixture",
            "actor": "local-tester",
            "revision": 2,
            "cancel_requested": False,
        }
    )
    result = await (await graphs.fundamentals({})).ainvoke(
        {"messages": [HumanMessage("ticker=DEMO horizon=short")], "work_revision": 2},
        context=context(revision=1, ledger_path=str(ledger.path)),
    )
    assert json.loads(result["messages"][-1].content)["synthetic"] is True
    assert result["work_revision"] == 2
    assert result["result_revision"] == 1


async def test_late_final_answer_records_its_own_revision_without_advancing_work(server):
    graph = await graphs.supervisor({})
    current = await graph.ainvoke(
        {"messages": [HumanMessage("¿Qué significa P/E?")]},
        context=context(revision=2, max_children=0),
    )
    assert current["result_revision"] == 2
    late_notice = HumanMessage(
        content="<task-notification>synthetic result from previous revision</task-notification>",
        additional_kwargs={
            "deepagents_notification": {
                "task_id": "older-child",
                "agent_name": "fundamentals",
                "status": "success",
            }
        },
    )
    late = await graph.ainvoke(
        {**current, "messages": [*current["messages"], late_notice]},
        context=context(revision=1, max_children=0),
    )
    assert late["work_revision"] == 2
    assert late["result_revision"] == 1
    assert "previous revision" in late["messages"][-1].content


def test_live_selection_builds_real_provider_model_without_network(monkeypatch):
    from langchain_openai import ChatOpenAI

    monkeypatch.setenv("FIN_ASYNC_MODEL", "openai:pilot-model")
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-not-a-real-key")
    model = graphs._model("supervisor")
    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "pilot-model"


async def test_model_mode_rejects_ambiguous_provider_selection(monkeypatch):
    monkeypatch.setenv("FIN_ASYNC_MODEL", "unknown-model")
    with pytest.raises(ValueError, match="provider:model"):
        await graphs.supervisor({})


async def test_final_notification_answer_keeps_both_children_evidence(server):
    graph = await graphs.supervisor({})
    state = await graph.ainvoke(
        {"messages": [HumanMessage("Analiza fundamentales y técnicos")]}, context=context()
    )
    for task_id, task in list(state["async_tasks"].items()):
        notice = HumanMessage(
            content=(
                f"<task-notification>synthetic evidence from {task['agent_name']}"
                "</task-notification>"
            ),
            additional_kwargs={
                "deepagents_notification": {
                    "task_id": task_id,
                    "agent_name": task["agent_name"],
                    "status": "success",
                    "run_id": task["run_id"],
                }
            },
        )
        state = await graph.ainvoke(
            {**state, "messages": [*state["messages"], notice]}, context=context()
        )
    assert "synthetic evidence from fundamentals" in state["messages"][-1].content
    assert "synthetic evidence from technicals" in state["messages"][-1].content


@pytest.mark.parametrize("consolidate", [False, True])
async def test_consolidation_uses_latest_notice_per_task_without_child_commands(
    server, consolidate
):
    graph = await graphs.supervisor({})
    initial = await graph.ainvoke(
        {"messages": [HumanMessage("Analiza fundamentales y técnicos")]},
        context=context(revision=1),
    )
    tasks = {task["agent_name"]: task for task in initial["async_tasks"].values()}
    notices = []
    for role, text in [
        ("technicals", "obsolete-technicals-result"),
        ("fundamentals", "current-fundamentals-result"),
        ("technicals", "current-technicals-result"),
    ]:
        task = tasks[role]
        task["status"] = "success"
        notices.append(
            HumanMessage(
                content=f"<task-notification>synthetic {text}</task-notification>",
                additional_kwargs={
                    "deepagents_notification": {
                        "task_id": task["task_id"],
                        "agent_name": role,
                        "status": "success",
                        "run_id": task["run_id"],
                    }
                },
            )
        )
    if consolidate:
        notices.append(
            HumanMessage(
                "Consolida los resultados actuales del trabajo sin iniciar ni actualizar hijos"
            )
        )
    result = await graph.ainvoke(
        {**initial, "messages": [*initial["messages"], *notices]},
        context=context(revision=2),
    )
    assert len(server.launches) == 2
    assert len(result["decision_trace"]) == len(initial["decision_trace"])
    final = result["messages"][-1]
    assert not final.tool_calls
    assert "current-technicals-result" in final.content
    assert "current-fundamentals-result" in final.content
    assert "obsolete-technicals-result" not in final.content
    assert result["result_revision"] == 2


@pytest.mark.parametrize("graph_id", ["supervisor", "fundamentals", "technicals"])
async def test_cli_config_resolves_executable_factories_from_repository_root(server, graph_id):
    from langgraph_cli.config import validate_config_file

    config = validate_config_file(Path("examples/langgraph.async.json"))
    module, variable = config["graphs"][graph_id].split(":")
    factory = getattr(importlib.import_module(module), variable)
    graph = await factory({})
    assert graph.checkpointer is None
    result = await graph.ainvoke(
        {"messages": [HumanMessage("ticker=FIXT horizon=short")]},
        context=context(max_children=0),
    )
    assert result["model_mode"] == "offline"
    assert not server.launches


async def test_live_provider_receives_no_tools_during_internal_consolidation(server, monkeypatch):
    from langchain_openai import ChatOpenAI

    requests = []

    def complete(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(
            200,
            json={
                "id": "completion-fixture",
                "object": "chat.completion",
                "created": 0,
                "model": "provider-contract-fixture",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Synthetic summary"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(complete)) as http:
        model = ChatOpenAI(
            model="provider-contract-fixture",
            api_key="fixture-not-a-real-key",
            base_url="http://provider.test/v1",
            http_async_client=http,
        )
        monkeypatch.setattr(graphs, "_model", lambda role: model)
        result = await (await graphs.supervisor({})).ainvoke(
            {"messages": [HumanMessage("Consolida los resultados actuales del trabajo")]},
            context=context(revision=2, consolidating=True),
        )
    assert len(requests) == 1
    assert not requests[0].get("tools")
    assert result["model_mode"] == "live"
    assert result["result_revision"] == 2
    assert result["messages"][-1].content == "Synthetic summary"
    assert not server.launches


@pytest.mark.parametrize(
    "tool_name,args",
    [
        ("start_async_task", {"subagent_type": "technicals", "description": "read"}),
        ("update_async_task", {"task_id": "any-child", "message": "change"}),
        ("cancel_async_task", {"task_id": "any-child"}),
        ("check_async_task", {"task_id": "any-child"}),
        ("list_async_tasks", {}),
    ],
)
async def test_consolidation_rejects_every_model_tool_call(server, monkeypatch, tool_name, args):
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="", tool_calls=[{"name": tool_name, "args": args, "id": "malicious"}]
            ),
            AIMessage(content="done"),
        ]
    )
    monkeypatch.setattr(graphs, "_model", lambda role: model)
    result = await (await graphs.supervisor({})).ainvoke(
        {"messages": [HumanMessage("Consolida los resultados actuales del trabajo")]},
        context=context(consolidating=True),
    )
    replies = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(replies) == 1
    assert replies[0].status == "error"
    assert "consolidation_read_only" in replies[0].content
    assert not server.launches and not server.cancellations
