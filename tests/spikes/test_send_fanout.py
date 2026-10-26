"""Spike: parallel specialists via Send, each a create_agent subagent, merged by a reducer."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel

from fin_graph.agents.factory import make_agent
from fin_graph.agents.testing.scripted_model import ScriptedChatModel, tool_call_message


class Finding(BaseModel):
    summary: str
    confidence: float


class InvestigationState(TypedDict, total=False):
    domains: list[str]
    findings: Annotated[list[dict[str, Any]], operator.add]


async def test_each_send_branch_runs_its_own_subagent_and_findings_merge() -> None:
    scripts = {
        "logs": [tool_call_message("Finding", {"summary": "OOMKilled", "confidence": 0.8}, "l")],
        "metrics": [
            tool_call_message("Finding", {"summary": "memory 99%", "confidence": 0.7}, "m")
        ],
    }
    specialists = {
        domain: make_agent(
            name=domain,
            model=ScriptedChatModel(responses=responses),
            response_format=ToolStrategy(Finding),
        )
        for domain, responses in scripts.items()
    }

    def plan(state: InvestigationState) -> dict[str, Any]:
        return {"domains": ["logs", "metrics"]}

    def dispatch(state: InvestigationState) -> list[Send]:
        return [Send("specialist", {"domain": domain}) for domain in state["domains"]]

    async def specialist(task: dict[str, Any]) -> dict[str, Any]:
        domain = task["domain"]
        result = await specialists[domain].ainvoke(
            {"messages": [HumanMessage(f"investigate {domain}")]}
        )
        return {"findings": [result["structured_response"].model_dump()]}

    graph = StateGraph(InvestigationState)
    graph.add_node("plan", plan)
    graph.add_node("specialist", specialist)
    graph.add_node("synthesize", lambda state: {})
    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", dispatch, ["specialist"])
    graph.add_edge("specialist", "synthesize")
    graph.add_edge("synthesize", END)

    result = await graph.compile().ainvoke({})

    assert sorted(f["summary"] for f in result["findings"]) == ["OOMKilled", "memory 99%"]
