from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, ToolException, tool

from fin_graph.agents.factory import make_agent
from fin_graph.agents.middleware.tool_cache import CACHE_SCOPE_KEY, ToolResultCacheMiddleware
from fin_graph.agents.testing.scripted_model import ScriptedChatModel, tool_call_message
from fin_graph.domain.tools import Domain, EvidenceType, SideEffectLevel, ToolMeta, attach_tool_meta


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _two_identical_calls(name: str = "get_pods") -> ScriptedChatModel:
    return ScriptedChatModel(
        responses=[
            tool_call_message(name, {"namespace": "payments"}, "first"),
            tool_call_message(name, {"namespace": "payments"}, "second"),
            AIMessage("final"),
        ]
    )


def _one_call(call_id: str) -> ScriptedChatModel:
    return ScriptedChatModel(
        responses=[
            tool_call_message("get_pods", {"namespace": "payments"}, call_id),
            AIMessage("final"),
        ]
    )


def _run_config(scope: str | None, thread_id: str = "t1") -> dict[str, Any]:
    configurable: dict[str, Any] = {"thread_id": thread_id}
    if scope is not None:
        configurable[CACHE_SCOPE_KEY] = scope
    return {"configurable": configurable}


def _meta(level: SideEffectLevel) -> ToolMeta:
    return ToolMeta(
        integration="test",
        domains=frozenset({Domain.KUBERNETES}),
        evidence_type=EvidenceType.TOPOLOGY,
        side_effect_level=level,
    )


def _read_only(declared: BaseTool) -> BaseTool:
    return attach_tool_meta(declared, _meta(SideEffectLevel.READ_ONLY))


def test_repeated_identical_read_only_call_is_served_from_cache() -> None:
    executions: list[str] = []

    @tool
    def get_pods(namespace: str) -> str:
        """List pods in a namespace."""
        executions.append(namespace)
        return f"3 pods in {namespace}"

    cache = ToolResultCacheMiddleware()
    agent = make_agent(
        name="k8s", model=_two_identical_calls(), tools=[_read_only(get_pods)], middleware=[cache]
    )

    result = agent.invoke({"messages": [HumanMessage("go")]}, _run_config("run-1"))

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert executions == ["payments"]
    assert cache.hits == 1
    assert [m.tool_call_id for m in tool_messages] == ["first", "second"]
    assert [m.content for m in tool_messages] == ["3 pods in payments", "3 pods in payments"]


async def test_async_runs_use_the_cache_too() -> None:
    executions: list[str] = []

    @tool
    async def get_pods(namespace: str) -> str:
        """List pods in a namespace."""
        executions.append(namespace)
        return f"3 pods in {namespace}"

    cache = ToolResultCacheMiddleware()
    agent = make_agent(
        name="k8s", model=_two_identical_calls(), tools=[_read_only(get_pods)], middleware=[cache]
    )

    await agent.ainvoke({"messages": [HumanMessage("go")]}, _run_config("run-1"))

    assert executions == ["payments"]
    assert cache.hits == 1


def test_a_new_run_on_the_same_thread_reaches_the_tool_again() -> None:
    executions: list[str] = []

    @tool
    def get_pods(namespace: str) -> str:
        """List pods in a namespace."""
        executions.append(namespace)
        return f"3 pods in {namespace}"

    cache = ToolResultCacheMiddleware()
    for turn in ("turn-1", "turn-2"):
        agent = make_agent(
            name="k8s", model=_one_call(turn), tools=[_read_only(get_pods)], middleware=[cache]
        )
        agent.invoke({"messages": [HumanMessage("go")]}, _run_config(turn, thread_id="chat-1"))

    assert executions == ["payments", "payments"]
    assert cache.hits == 0


def test_calls_without_a_scope_are_never_cached() -> None:
    executions: list[str] = []

    @tool
    def get_pods(namespace: str) -> str:
        """List pods in a namespace."""
        executions.append(namespace)
        return f"3 pods in {namespace}"

    cache = ToolResultCacheMiddleware()
    agent = make_agent(
        name="k8s", model=_two_identical_calls(), tools=[_read_only(get_pods)], middleware=[cache]
    )

    agent.invoke({"messages": [HumanMessage("go")]}, _run_config(None))

    assert executions == ["payments", "payments"]
    assert cache.hits == 0


@pytest.mark.parametrize("level", [None, SideEffectLevel.MUTATING, SideEffectLevel.EXTERNAL])
def test_tools_not_declared_read_only_always_run(level: SideEffectLevel | None) -> None:
    restarts: list[str] = []

    @tool
    def rollout_restart(namespace: str) -> str:
        """Restart every deployment in a namespace."""
        restarts.append(namespace)
        return f"restarted {namespace}"

    if level is not None:
        attach_tool_meta(rollout_restart, _meta(level))
    cache = ToolResultCacheMiddleware()
    agent = make_agent(
        name="ops",
        model=_two_identical_calls("rollout_restart"),
        tools=[rollout_restart],
        middleware=[cache],
    )

    agent.invoke({"messages": [HumanMessage("go")]}, _run_config("run-1"))

    assert restarts == ["payments", "payments"]
    assert cache.hits == 0


def test_entries_expire_after_the_ttl() -> None:
    executions: list[str] = []

    @tool
    def get_pods(namespace: str) -> str:
        """List pods in a namespace."""
        executions.append(namespace)
        return f"3 pods in {namespace}"

    clock = FakeClock()
    cache = ToolResultCacheMiddleware(ttl_seconds=300, clock=clock)
    read_only_pods = _read_only(get_pods)
    make_agent(name="k8s", model=_one_call("a"), tools=[read_only_pods], middleware=[cache]).invoke(
        {"messages": [HumanMessage("go")]}, _run_config("run-1")
    )
    clock.now = 301.0
    make_agent(name="k8s", model=_one_call("b"), tools=[read_only_pods], middleware=[cache]).invoke(
        {"messages": [HumanMessage("go")]}, _run_config("run-1")
    )

    assert executions == ["payments", "payments"]
    assert cache.hits == 0


def test_error_results_are_retried_not_cached() -> None:
    attempts: list[str] = []

    @tool
    def get_pods(namespace: str) -> str:
        """List pods in a namespace."""
        attempts.append(namespace)
        raise ToolException("cluster unreachable")

    get_pods.handle_tool_error = True
    cache = ToolResultCacheMiddleware()
    agent = make_agent(
        name="k8s", model=_two_identical_calls(), tools=[_read_only(get_pods)], middleware=[cache]
    )

    result = agent.invoke({"messages": [HumanMessage("go")]}, _run_config("run-1"))

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert attempts == ["payments", "payments"]
    assert cache.hits == 0
    assert all(m.status == "error" for m in tool_messages)


def test_same_arguments_in_a_different_order_hit_the_cache() -> None:
    executions: list[tuple[str, int]] = []

    @tool
    def get_logs(service: str, limit: int) -> str:
        """Fetch recent log lines for a service."""
        executions.append((service, limit))
        return f"{limit} lines from {service}"

    model = ScriptedChatModel(
        responses=[
            tool_call_message("get_logs", {"service": "checkout", "limit": 50}, "first"),
            tool_call_message("get_logs", {"limit": 50, "service": "checkout"}, "second"),
            AIMessage("final"),
        ]
    )
    cache = ToolResultCacheMiddleware()
    agent = make_agent(name="logs", model=model, tools=[_read_only(get_logs)], middleware=[cache])

    agent.invoke({"messages": [HumanMessage("go")]}, _run_config("run-1"))

    assert executions == [("checkout", 50)]
    assert cache.hits == 1
