"""Spike: runtime context is not checkpointed, so every resume must pass it again."""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt


@dataclass
class RunContext:
    tenant: str


class ContextState(TypedDict, total=False):
    seen: Annotated[list[str], operator.add]


def _graph() -> Any:
    def node(state: ContextState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        tenant = runtime.context.tenant if runtime.context is not None else "<missing>"
        interrupt("pause")
        return {"seen": [tenant]}

    graph = StateGraph(ContextState, context_schema=RunContext)
    graph.add_node("node", node)
    graph.add_edge(START, "node")
    graph.add_edge("node", END)
    return graph.compile(checkpointer=InMemorySaver())


def test_resume_without_context_runs_with_none() -> None:
    app = _graph()
    config = {"configurable": {"thread_id": "no-context"}}

    app.invoke({}, config, context=RunContext("acme"))
    resumed = app.invoke(Command(resume="go"), config)

    assert resumed["seen"] == ["<missing>"]


def test_resume_with_context_sees_it() -> None:
    app = _graph()
    config = {"configurable": {"thread_id": "with-context"}}

    app.invoke({}, config, context=RunContext("acme"))
    resumed = app.invoke(Command(resume="go"), config, context=RunContext("acme"))

    assert resumed["seen"] == ["acme"]
