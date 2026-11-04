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

EMAIL = "analyst@fund.example"
POLICY = MaskingPolicy(enabled=True)
EMAIL_TOKEN = Masker(POLICY).mask(EMAIL)


class Finding(BaseModel):
    summary: str


@dataclass
class DataclassFinding:
    summary: str


def _request_filing(received: list[str]) -> BaseTool:
    @tool
    def request_filing(email: str) -> str:
        """Request a filing from an investor-relations email."""
        received.append(email)
        return f"{email}: filing requested"

    return request_filing


def _masked_agent(
    model: ScriptedChatModel, **kwargs: Any
) -> CompiledStateGraph[Any, Any, Any, Any]:
    return make_agent(
        name="fundamentals", model=model, middleware=[MaskingMiddleware(POLICY)], **kwargs
    )


def test_the_token_is_an_email_token() -> None:
    assert re.fullmatch(r"<EMAIL_[0-9a-f]{10}>", EMAIL_TOKEN)


def test_the_model_sees_tokens_and_the_tool_receives_the_real_email() -> None:
    received: list[str] = []
    model = ScriptedChatModel(
        responses=[
            tool_call_message("request_filing", {"email": EMAIL_TOKEN}, "c1"),
            AIMessage(f"{EMAIL_TOKEN}: filing requested"),
        ]
    )
    agent = _masked_agent(model, tools=[_request_filing(received)])

    result = agent.invoke({"messages": [HumanMessage(f"request a filing from {EMAIL}")]})

    assert received == [EMAIL]
    assert EMAIL not in str([message.content for call in model.calls for message in call])
    assert result["messages"][-1].content == f"{EMAIL}: filing requested"


def test_the_system_prompt_is_masked_too() -> None:
    model = ScriptedChatModel(responses=[AIMessage("ok")])
    agent = _masked_agent(model, system_prompt=f"Contact {EMAIL}.")

    agent.invoke({"messages": [HumanMessage("go")]})

    assert model.calls[0][0].content == f"Contact {EMAIL_TOKEN}."


def test_a_pydantic_structured_response_is_unmasked() -> None:
    model = ScriptedChatModel(
        responses=[tool_call_message("Finding", {"summary": f"{EMAIL_TOKEN} replied"}, "s1")]
    )
    agent = _masked_agent(model, response_format=ToolStrategy(Finding))

    result = agent.invoke({"messages": [HumanMessage(f"contact {EMAIL}")]})

    assert result["structured_response"] == Finding(summary=f"{EMAIL} replied")


def test_a_dataclass_structured_response_is_unmasked() -> None:
    model = ScriptedChatModel(
        responses=[
            tool_call_message("DataclassFinding", {"summary": f"{EMAIL_TOKEN} replied"}, "s1")
        ]
    )
    agent = _masked_agent(model, response_format=ToolStrategy(DataclassFinding))

    result = agent.invoke({"messages": [HumanMessage(f"contact {EMAIL}")]})

    assert result["structured_response"] == DataclassFinding(summary=f"{EMAIL} replied")


def test_a_disabled_policy_sends_real_values() -> None:
    model = ScriptedChatModel(responses=[AIMessage("ok")])
    agent = make_agent(
        name="fundamentals",
        model=model,
        middleware=[MaskingMiddleware(MaskingPolicy(enabled=False))],
    )

    agent.invoke({"messages": [HumanMessage(f"contact {EMAIL}")]})

    assert model.calls[0][-1].content == f"contact {EMAIL}"


async def test_masking_also_applies_to_async_runs() -> None:
    received: list[str] = []
    model = ScriptedChatModel(
        responses=[
            tool_call_message("request_filing", {"email": EMAIL_TOKEN}, "c1"),
            AIMessage("done"),
        ]
    )
    agent = _masked_agent(model, tools=[_request_filing(received)])

    await agent.ainvoke({"messages": [HumanMessage(f"request a filing from {EMAIL}")]})

    assert received == [EMAIL]
    assert EMAIL not in str(model.calls[0][-1].content)


def test_portfolios_and_emails_in_json_results_and_tool_args_are_masked() -> None:
    portfolio_token = Masker(POLICY).mask("portfolio id PF-2026-001").split()[-1]

    @tool
    def describe_portfolio(portfolio_id: str) -> str:
        """Describe a paper portfolio."""
        return json.dumps({"portfolio_id": portfolio_id, "owner": {"email": EMAIL}})

    model = ScriptedChatModel(
        responses=[
            tool_call_message("describe_portfolio", {"portfolio_id": portfolio_token}, "c1"),
            AIMessage("done"),
        ]
    )
    agent = _masked_agent(model, tools=[describe_portfolio])

    agent.invoke({"messages": [HumanMessage("check portfolio id PF-2026-001")]})

    sent = str([(m.content, getattr(m, "tool_calls", None)) for m in model.calls[1]])
    assert "PF-2026-001" not in sent
    assert EMAIL not in sent
