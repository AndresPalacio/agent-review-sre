"""Spike: an interrupt inside a compiled subgraph used as a node pauses and resumes the parent."""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


class OrderApprovalState(TypedDict, total=False):
    order: str
    decision: str
    executed: list[str]


def _order_approval_subgraph() -> Any:
    def approve(state: OrderApprovalState) -> dict[str, Any]:
        return {"decision": interrupt({"approve": state["order"]})}

    def execute(state: OrderApprovalState) -> dict[str, Any]:
        done = [state["order"]] if state["decision"] == "approve" else []
        return {"executed": done}

    graph = StateGraph(OrderApprovalState)
    graph.add_node("approve", approve)
    graph.add_node("execute", execute)
    graph.add_edge(START, "approve")
    graph.add_edge("approve", "execute")
    graph.add_edge("execute", END)
    return graph.compile()


def _parent() -> Any:
    graph = StateGraph(OrderApprovalState)
    graph.add_node("propose", lambda state: {"order": "buy 1 MSFT in paper portfolio"})
    graph.add_node("approve_order", _order_approval_subgraph())
    graph.add_edge(START, "propose")
    graph.add_edge("propose", "approve_order")
    graph.add_edge("approve_order", END)
    return graph.compile(checkpointer=InMemorySaver())


def test_parent_pauses_on_the_subgraph_interrupt_and_resumes_through_it() -> None:
    app = _parent()
    config = {"configurable": {"thread_id": "order-approval"}}

    paused = app.invoke({}, config)
    resumed = app.invoke(Command(resume="approve"), config)

    assert paused["__interrupt__"][0].value == {"approve": "buy 1 MSFT in paper portfolio"}
    assert resumed["executed"] == ["buy 1 MSFT in paper portfolio"]


def test_a_rejected_decision_executes_nothing() -> None:
    app = _parent()
    config = {"configurable": {"thread_id": "rejected"}}

    app.invoke({}, config)
    resumed = app.invoke(Command(resume="reject"), config)

    assert resumed["executed"] == []
