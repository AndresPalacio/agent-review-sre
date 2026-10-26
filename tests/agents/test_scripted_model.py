from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from fin_graph.agents.factory import make_agent
from fin_graph.agents.testing.scripted_model import (
    ScriptedChatModel,
    ScriptedModelExhaustedError,
    tool_call_message,
)


@tool
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


def test_replays_responses_in_order_and_records_each_call() -> None:
    model = ScriptedChatModel(responses=[AIMessage("one"), AIMessage("two")])

    assert model.invoke("first").content == "one"
    assert model.invoke("second").content == "two"
    assert model.remaining == 0
    assert [call[-1].content for call in model.calls] == ["first", "second"]


def test_running_out_of_script_raises_a_named_error() -> None:
    model = ScriptedChatModel(responses=[])

    with pytest.raises(ScriptedModelExhaustedError):
        model.invoke("anything")


def test_binding_tools_keeps_the_same_script() -> None:
    model = ScriptedChatModel(responses=[tool_call_message("add", {"a": 1, "b": 2}, "c1")])

    reply = model.bind_tools([add]).invoke("add please")

    assert isinstance(reply, AIMessage)
    assert reply.tool_calls[0]["name"] == "add"
    assert reply.tool_calls[0]["args"] == {"a": 1, "b": 2}


def test_reusing_one_scripted_message_keeps_both_replies_in_the_thread() -> None:
    reply = AIMessage("ok")
    model = ScriptedChatModel(responses=[reply, reply])
    agent = make_agent(name="echo", model=model, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "reuse"}}

    agent.invoke({"messages": [HumanMessage("one")]}, config)
    second = agent.invoke({"messages": [HumanMessage("two")]}, config)

    assert len(second["messages"]) == 4
