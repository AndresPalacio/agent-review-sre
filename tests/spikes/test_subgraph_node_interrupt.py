"""Spike: an interrupt inside a compiled subgraph used as a node pauses and resumes the parent."""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


class RemediationState(TypedDict, total=False):
    action: str
    decision: str
    executed: list[str]


def _remediation_subgraph() -> Any:
    def approve(state: RemediationState) -> dict[str, Any]:
        return {"decision": interrupt({"approve": state["action"]})}

    def execute(state: RemediationState) -> dict[str, Any]:
        done = [state["action"]] if state["decision"] == "approve" else []
        return {"executed": done}

    graph = StateGraph(RemediationState)
    graph.add_node("approve", approve)
    graph.add_node("execute", execute)
    graph.add_edge(START, "approve")
    graph.add_edge("approve", "execute")
    graph.add_edge("execute", END)
    return graph.compile()


def _parent() -> Any:
    graph = StateGraph(RemediationState)
    graph.add_node("propose", lambda state: {"action": "rollout-undo api"})
    graph.add_node("remediate", _remediation_subgraph())
    graph.add_edge(START, "propose")
    graph.add_edge("propose", "remediate")
    graph.add_edge("remediate", END)
    return graph.compile(checkpointer=InMemorySaver())


def test_parent_pauses_on_the_subgraph_interrupt_and_resumes_through_it() -> None:
    app = _parent()
    config = {"configurable": {"thread_id": "remediation"}}

    paused = app.invoke({}, config)
    resumed = app.invoke(Command(resume="approve"), config)

    assert paused["__interrupt__"][0].value == {"approve": "rollout-undo api"}
    assert resumed["executed"] == ["rollout-undo api"]


def test_a_rejected_decision_executes_nothing() -> None:
    app = _parent()
    config = {"configurable": {"thread_id": "rejected"}}

    app.invoke({}, config)
    resumed = app.invoke(Command(resume="reject"), config)

    assert resumed["executed"] == []
