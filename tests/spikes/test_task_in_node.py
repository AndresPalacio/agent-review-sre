"""Spike: a node re-runs from the top on resume; only @task results are replayed, not redone."""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.func import task
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

EXECUTED_ACTIONS: list[str] = []


@task
def execute_action(action_id: str) -> str:
    EXECUTED_ACTIONS.append(action_id)
    return f"done:{action_id}"


class ExecutionState(TypedDict, total=False):
    results: list[str]
    decision: Any


def _single_node_graph(node: Any) -> Any:
    graph = StateGraph(ExecutionState)
    graph.add_node("execute", node)
    graph.add_edge(START, "execute")
    graph.add_edge("execute", END)
    return graph.compile(checkpointer=InMemorySaver())


def test_task_side_effect_is_not_repeated_when_the_node_resumes() -> None:
    EXECUTED_ACTIONS.clear()

    def execute(state: ExecutionState) -> dict[str, Any]:
        result = execute_action("rollback-1").result()
        decision = interrupt({"confirm_after": result})
        return {"results": [result], "decision": decision}

    app = _single_node_graph(execute)
    config = {"configurable": {"thread_id": "task"}}

    app.invoke({}, config)
    resumed = app.invoke(Command(resume="ok"), config)

    assert EXECUTED_ACTIONS == ["rollback-1"]
    assert resumed["results"] == ["done:rollback-1"]


def test_plain_code_before_interrupt_runs_again_on_resume() -> None:
    calls: list[str] = []

    def execute(state: ExecutionState) -> dict[str, Any]:
        calls.append("side effect")
        return {"decision": interrupt("approve?")}

    app = _single_node_graph(execute)
    config = {"configurable": {"thread_id": "plain"}}

    app.invoke({}, config)
    app.invoke(Command(resume="ok"), config)

    assert calls == ["side effect", "side effect"]
