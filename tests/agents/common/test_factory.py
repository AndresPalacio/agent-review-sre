from __future__ import annotations

from dataclasses import dataclass

from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel

from fin_graph.agents.common.factory import make_agent
from fin_graph.agents.common.testing.scripted_model import ScriptedChatModel, tool_call_message


class Finding(BaseModel):
    summary: str
    confidence: float


@dataclass
class TenantContext:
    tenant: str


@tool
def noop() -> str:
    """Do nothing."""
    return "ok"


def test_structured_response_comes_back_as_the_schema_type() -> None:
    model = ScriptedChatModel(
        responses=[tool_call_message("Finding", {"summary": "oom", "confidence": 0.9}, "s1")]
    )
    agent = make_agent(name="analyst", model=model, response_format=ToolStrategy(Finding))

    result = agent.invoke({"messages": [HumanMessage("analyze")]})

    assert result["structured_response"] == Finding(summary="oom", confidence=0.9)


def test_middleware_caps_a_looping_model() -> None:
    model = ScriptedChatModel(responses=[tool_call_message("noop", {}, f"c{i}") for i in range(12)])
    agent = make_agent(
        name="looper",
        model=model,
        tools=[noop],
        middleware=[ModelCallLimitMiddleware(run_limit=2, exit_behavior="end")],
    )

    agent.invoke({"messages": [HumanMessage("go")]})

    assert len(model.calls) == 2


def test_tools_read_the_runtime_context() -> None:
    @tool
    def whoami(runtime: ToolRuntime[TenantContext]) -> str:
        """Return the tenant this run belongs to."""
        return runtime.context.tenant

    model = ScriptedChatModel(responses=[tool_call_message("whoami", {}, "w1"), AIMessage("done")])
    agent = make_agent(name="ctx", model=model, tools=[whoami], context_schema=TenantContext)

    result = agent.invoke({"messages": [HumanMessage("who")]}, context=TenantContext("acme"))

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert [m.content for m in tool_messages] == ["acme"]
