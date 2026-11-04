from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from langchain.agents.structured_output import ToolStrategy
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, PrivateAttr

from fin_graph.agents.common.factory import make_agent
from fin_graph.agents.common.subagents import SubagentSpec, make_supervisor
from fin_graph.agents.common.testing.scripted_model import ScriptedChatModel, tool_call_message


@tool
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


class Finding(BaseModel):
    summary: str
    confidence: float


@dataclass
class TenantContext:
    tenant: str


class TaskInspectingModel(ScriptedChatModel):
    _task_descriptions: list[str] = PrivateAttr(default_factory=list)
    _task_schemas: list[dict[str, Any]] = PrivateAttr(default_factory=list)

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> TaskInspectingModel:
        task_tool = next(candidate for candidate in tools if candidate.name == "task")
        self._task_descriptions.append(task_tool.description)
        self._task_schemas.append(task_tool.get_input_schema().model_json_schema())
        return self

    @property
    def task_descriptions(self) -> list[str]:
        return self._task_descriptions

    @property
    def task_schemas(self) -> list[dict[str, Any]]:
        return self._task_schemas


def _tool_contents(result: dict[str, object]) -> list[object]:
    messages = result["messages"]
    assert isinstance(messages, list)
    return [m.content for m in messages if isinstance(m, ToolMessage)]


async def test_task_offers_only_declared_subagents_and_runs_the_selected_child() -> None:
    specialist_model = ScriptedChatModel(responses=[AIMessage("Declared child ran.")])
    supervisor_model = TaskInspectingModel(
        responses=[
            tool_call_message(
                "task",
                {"description": "handle the declared work", "subagent_type": "declared_child"},
                "c1",
            ),
            AIMessage("Delegation complete."),
        ]
    )
    specialist = make_agent(name="declared_child", model=specialist_model)
    app = make_supervisor(
        subagents=[SubagentSpec("declared_child", "Handles declared work.", specialist)],
        model=supervisor_model,
    )

    result = await app.ainvoke({"messages": [HumanMessage("delegate this")]})

    assert supervisor_model.task_descriptions
    assert all(
        "- declared_child: Handles declared work." in description
        for description in supervisor_model.task_descriptions
    )
    assert all(
        "- general-purpose:" not in description
        for description in supervisor_model.task_descriptions
    )
    assert supervisor_model.task_schemas[0]["required"] == ["description", "subagent_type"]
    assert _tool_contents(result) == ["Declared child ran."]
    assert result["messages"][-1].content == "Delegation complete."


async def test_supervisor_sees_only_each_subagents_final_answer() -> None:
    model = ScriptedChatModel(
        responses=[
            tool_call_message(
                "task",
                {"description": "sum 10 and 15", "subagent_type": "math_expert"},
                "c1",
            ),
            tool_call_message("add", {"a": 10, "b": 15}, "c2"),
            AIMessage("The sum of 10 and 15 is 25."),
            tool_call_message(
                "task",
                {"description": "One line: 10 + 15 = 25.", "subagent_type": "writer"},
                "c3",
            ),
            AIMessage("10 + 15 = 25, tidy."),
            AIMessage("Sum is 25. 10 + 15 = 25, tidy."),
        ]
    )
    math = make_agent(name="math_expert", model=model, tools=[add])
    writer = make_agent(name="writer", model=model)
    app = make_supervisor(
        subagents=[
            SubagentSpec("math_expert", "Delegate calculations.", math),
            SubagentSpec("writer", "Delegate writing.", writer),
        ],
        model=model,
        checkpointer=InMemorySaver(),
    )

    result = await app.ainvoke(
        {"messages": [HumanMessage("sum 10 and 15, then summarize")]},
        {"configurable": {"thread_id": "delegation"}},
    )

    assert result["messages"][-1].content == "Sum is 25. 10 + 15 = 25, tidy."
    assert _tool_contents(result) == ["The sum of 10 and 15 is 25.", "10 + 15 = 25, tidy."]


def test_synchronous_invoke_also_delegates() -> None:
    model = ScriptedChatModel(
        responses=[
            tool_call_message(
                "task",
                {"description": "sum 1 and 2", "subagent_type": "math_expert"},
                "c1",
            ),
            AIMessage("3"),
            AIMessage("The answer is 3."),
        ]
    )
    math = make_agent(name="math_expert", model=model, tools=[add])
    app = make_supervisor(
        subagents=[SubagentSpec("math_expert", "Delegate calculations.", math)], model=model
    )

    result = app.invoke({"messages": [HumanMessage("1 + 2?")]})

    assert result["messages"][-1].content == "The answer is 3."
    assert _tool_contents(result) == ["3"]


async def test_conversation_memory_lives_on_the_supervisor_thread() -> None:
    model = ScriptedChatModel(responses=[AIMessage("Hello!"), AIMessage("You said hi.")])
    worker = make_agent(name="worker", model=model)
    app = make_supervisor(
        subagents=[SubagentSpec("worker", "Does work.", worker)],
        model=model,
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "memory"}}

    await app.ainvoke({"messages": [HumanMessage("hi")]}, config)
    second = await app.ainvoke({"messages": [HumanMessage("what did I say?")]}, config)

    assert len(second["messages"]) == 4


def _approval_app(outcomes: list[str]) -> CompiledStateGraph[Any, Any, Any, Any]:
    """A supervisor whose subagent's tool pauses for approval and records what it did."""

    @tool
    def delete_record(record_id: str) -> str:
        """Delete a record after human approval."""
        decision = interrupt({"type": "approval_required", "record_id": record_id})
        outcome = f"Record {record_id} deleted." if decision == "yes" else "Deletion rejected."
        outcomes.append(outcome)
        return outcome

    model = ScriptedChatModel(
        responses=[
            tool_call_message(
                "task",
                {"description": "delete rec_2", "subagent_type": "db_admin"},
                "c1",
            ),
            tool_call_message("delete_record", {"record_id": "rec_2"}, "c2"),
            AIMessage("The deletion was handled."),
            AIMessage("Done."),
        ]
    )
    admin = make_agent(name="db_admin", model=model, tools=[delete_record])
    return make_supervisor(
        subagents=[SubagentSpec("db_admin", "Manages records.", admin)],
        model=model,
        checkpointer=InMemorySaver(),
    )


async def test_interrupt_in_a_subagent_tool_pauses_and_resumes_the_supervisor() -> None:
    outcomes: list[str] = []
    app = _approval_app(outcomes)
    config = {"configurable": {"thread_id": "approval"}}

    paused = await app.ainvoke({"messages": [HumanMessage("delete rec_2")]}, config)
    snapshot = await app.aget_state(config)
    pending = [i.value for task in snapshot.tasks for i in task.interrupts]

    assert "__interrupt__" in paused
    assert snapshot.next
    assert pending == [{"type": "approval_required", "record_id": "rec_2"}]
    assert outcomes == []

    resumed = await app.ainvoke(Command(resume="yes"), config)

    assert outcomes == ["Record rec_2 deleted."]
    assert resumed["messages"][-1].content == "Done."


async def test_a_rejected_approval_reaches_the_subagent_tool() -> None:
    outcomes: list[str] = []
    app = _approval_app(outcomes)
    config = {"configurable": {"thread_id": "rejection"}}

    await app.ainvoke({"messages": [HumanMessage("delete rec_2")]}, config)
    await app.ainvoke(Command(resume="no"), config)

    assert outcomes == ["Deletion rejected."]


async def test_structured_subagent_result_reaches_the_supervisor_as_json() -> None:
    specialist_model = ScriptedChatModel(
        responses=[tool_call_message("Finding", {"summary": "OOMKilled", "confidence": 0.8}, "f1")]
    )
    supervisor_model = ScriptedChatModel(
        responses=[
            tool_call_message(
                "task",
                {"description": "check logs", "subagent_type": "logs_specialist"},
                "c1",
            ),
            AIMessage("Logs show OOMKilled."),
        ]
    )
    specialist = make_agent(
        name="logs_specialist", model=specialist_model, response_format=ToolStrategy(Finding)
    )
    app = make_supervisor(
        subagents=[SubagentSpec("logs_specialist", "Reads logs.", specialist)],
        model=supervisor_model,
    )

    result = await app.ainvoke({"messages": [HumanMessage("why did it crash?")]})

    [content] = _tool_contents(result)
    assert isinstance(content, str)
    assert json.loads(content) == {"summary": "OOMKilled", "confidence": 0.8}


async def test_runtime_context_reaches_the_subagents_tools() -> None:
    seen: list[str] = []

    @tool
    def whoami(runtime: ToolRuntime[TenantContext]) -> str:
        """Report which tenant this run belongs to."""
        seen.append(runtime.context.tenant)
        return runtime.context.tenant

    model = ScriptedChatModel(
        responses=[
            tool_call_message(
                "task",
                {"description": "who am I?", "subagent_type": "identity"},
                "c1",
            ),
            tool_call_message("whoami", {}, "c2"),
            AIMessage("You are acme."),
            AIMessage("Tenant: acme."),
        ]
    )
    identity = make_agent(
        name="identity", model=model, tools=[whoami], context_schema=TenantContext
    )
    app = make_supervisor(
        subagents=[SubagentSpec("identity", "Knows the tenant.", identity)],
        model=model,
        context_schema=TenantContext,
    )

    await app.ainvoke({"messages": [HumanMessage("tenant?")]}, context=TenantContext("acme"))

    assert seen == ["acme"]


def test_synchronous_invoke_forwards_runtime_context_too() -> None:
    seen: list[str] = []

    @tool
    def whoami(runtime: ToolRuntime[TenantContext]) -> str:
        """Report which tenant this run belongs to."""
        seen.append(runtime.context.tenant)
        return runtime.context.tenant

    model = ScriptedChatModel(
        responses=[
            tool_call_message(
                "task",
                {"description": "who am I?", "subagent_type": "identity"},
                "c1",
            ),
            tool_call_message("whoami", {}, "c2"),
            AIMessage("You are acme."),
            AIMessage("Tenant: acme."),
        ]
    )
    identity = make_agent(
        name="identity", model=model, tools=[whoami], context_schema=TenantContext
    )
    app = make_supervisor(
        subagents=[SubagentSpec("identity", "Knows the tenant.", identity)],
        model=model,
        context_schema=TenantContext,
    )

    app.invoke({"messages": [HumanMessage("tenant?")]}, context=TenantContext("acme"))

    assert seen == ["acme"]


def test_only_the_text_of_a_multi_block_reply_reaches_the_supervisor() -> None:
    reply = AIMessage(
        content=[
            {"type": "thinking", "thinking": "internal notes", "signature": "sig-123"},
            {"type": "text", "text": "Logs show OOMKilled."},
        ]
    )
    model = ScriptedChatModel(
        responses=[
            tool_call_message(
                "task",
                {"description": "check logs", "subagent_type": "logs_specialist"},
                "c1",
            ),
            reply,
            AIMessage("The logs show OOMKilled."),
        ]
    )
    specialist = make_agent(name="logs_specialist", model=model)
    app = make_supervisor(
        subagents=[SubagentSpec("logs_specialist", "Reads logs.", specialist)], model=model
    )

    result = app.invoke({"messages": [HumanMessage("why did it crash?")]})

    assert result["messages"][-1].content == "The logs show OOMKilled."
    assert _tool_contents(result) == ["Logs show OOMKilled."]


@dataclass
class DataclassFinding:
    summary: str
    confidence: float


class DataclassFindingState(MessagesState):
    structured_response: DataclassFinding | None


def test_a_dataclass_structured_response_is_sent_as_json() -> None:
    def respond(_state: DataclassFindingState) -> dict[str, object]:
        return {
            "messages": [AIMessage("Finding ready.")],
            "structured_response": DataclassFinding("OOMKilled", 0.8),
        }

    builder = StateGraph(DataclassFindingState)
    builder.add_node("respond", respond)
    builder.set_entry_point("respond")
    specialist = builder.compile()
    model = ScriptedChatModel(
        responses=[
            tool_call_message(
                "task",
                {"description": "check logs", "subagent_type": "logs_specialist"},
                "c1",
            ),
            AIMessage("Logs show OOMKilled."),
        ]
    )
    app = make_supervisor(
        subagents=[SubagentSpec("logs_specialist", "Reads logs.", specialist)], model=model
    )

    result = app.invoke({"messages": [HumanMessage("why did it crash?")]})

    [content] = _tool_contents(result)
    assert isinstance(content, str)
    assert json.loads(content) == {"summary": "OOMKilled", "confidence": 0.8}
