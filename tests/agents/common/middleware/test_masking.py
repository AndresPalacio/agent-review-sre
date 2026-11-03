from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import BaseTool, tool
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel

from fin_graph.agents.common.factory import make_agent
from fin_graph.agents.common.middleware.masking import MaskingMiddleware
from fin_graph.agents.common.testing.scripted_model import ScriptedChatModel, tool_call_message
from fin_graph.safety.masking import Masker, MaskingPolicy

POD = "checkout-7d9f8b6c5-x2x9z"
POLICY = MaskingPolicy(enabled=True)
POD_TOKEN = Masker(POLICY).mask(POD)


class Finding(BaseModel):
    summary: str


@dataclass
class DataclassFinding:
    summary: str


def _pod_logs(received: list[str]) -> BaseTool:
    @tool
    def pod_logs(pod: str) -> str:
        """Fetch a pod's logs."""
        received.append(pod)
        return f"{pod}: OOMKilled"

    return pod_logs


def _masked_agent(
    model: ScriptedChatModel, **kwargs: Any
) -> CompiledStateGraph[Any, Any, Any, Any]:
    return make_agent(name="k8s", model=model, middleware=[MaskingMiddleware(POLICY)], **kwargs)


def test_the_token_is_a_pod_token() -> None:
    assert re.fullmatch(r"<POD_[0-9a-f]{10}>", POD_TOKEN)


def test_the_model_sees_tokens_and_the_tool_receives_the_real_pod() -> None:
    received: list[str] = []
    model = ScriptedChatModel(
        responses=[
            tool_call_message("pod_logs", {"pod": POD_TOKEN}, "c1"),
            AIMessage(f"{POD_TOKEN} was OOMKilled"),
        ]
    )
    agent = _masked_agent(model, tools=[_pod_logs(received)])

    result = agent.invoke({"messages": [HumanMessage(f"why does {POD} restart?")]})

    assert received == [POD]
    assert POD not in str([message.content for call in model.calls for message in call])
    assert result["messages"][-1].content == f"{POD} was OOMKilled"


def test_the_system_prompt_is_masked_too() -> None:
    model = ScriptedChatModel(responses=[AIMessage("ok")])
    agent = _masked_agent(model, system_prompt=f"Focus on {POD}.")

    agent.invoke({"messages": [HumanMessage("go")]})

    assert model.calls[0][0].content == f"Focus on {POD_TOKEN}."


def test_a_pydantic_structured_response_is_unmasked() -> None:
    model = ScriptedChatModel(
        responses=[tool_call_message("Finding", {"summary": f"{POD_TOKEN} leaks"}, "s1")]
    )
    agent = _masked_agent(model, response_format=ToolStrategy(Finding))

    result = agent.invoke({"messages": [HumanMessage(f"look at {POD}")]})

    assert result["structured_response"] == Finding(summary=f"{POD} leaks")


def test_a_dataclass_structured_response_is_unmasked() -> None:
    model = ScriptedChatModel(
        responses=[tool_call_message("DataclassFinding", {"summary": f"{POD_TOKEN} leaks"}, "s1")]
    )
    agent = _masked_agent(model, response_format=ToolStrategy(DataclassFinding))

    result = agent.invoke({"messages": [HumanMessage(f"look at {POD}")]})

    assert result["structured_response"] == DataclassFinding(summary=f"{POD} leaks")


def test_a_disabled_policy_sends_real_values() -> None:
    model = ScriptedChatModel(responses=[AIMessage("ok")])
    agent = make_agent(
        name="k8s", model=model, middleware=[MaskingMiddleware(MaskingPolicy(enabled=False))]
    )

    agent.invoke({"messages": [HumanMessage(f"look at {POD}")]})

    assert model.calls[0][-1].content == f"look at {POD}"


async def test_masking_also_applies_to_async_runs() -> None:
    received: list[str] = []
    model = ScriptedChatModel(
        responses=[tool_call_message("pod_logs", {"pod": POD_TOKEN}, "c1"), AIMessage("done")]
    )
    agent = _masked_agent(model, tools=[_pod_logs(received)])

    await agent.ainvoke({"messages": [HumanMessage(f"why does {POD} restart?")]})

    assert received == [POD]
    assert POD not in str(model.calls[0][-1].content)


def test_namespaces_and_apps_in_json_results_and_tool_args_are_masked() -> None:
    namespace_token = Masker(POLICY).mask("namespace payments").split()[-1]

    @tool
    def describe_namespace(namespace: str) -> str:
        """Describe a namespace."""
        return json.dumps({"namespace": namespace, "labels": {"app": "checkout"}})

    model = ScriptedChatModel(
        responses=[
            tool_call_message("describe_namespace", {"namespace": namespace_token}, "c1"),
            AIMessage("done"),
        ]
    )
    agent = _masked_agent(model, tools=[describe_namespace])

    agent.invoke({"messages": [HumanMessage("check namespace payments")]})

    sent = str([(m.content, getattr(m, "tool_calls", None)) for m in model.calls[1]])
    assert "payments" not in sent
    assert "checkout" not in sent
