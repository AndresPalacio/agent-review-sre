"""Spike: parallel specialists via Send, each a create_agent subagent, merged by a reducer."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel

from fin_graph.agents.common.factory import make_agent
from fin_graph.agents.common.testing.scripted_model import ScriptedChatModel, tool_call_message


class Finding(BaseModel):
    summary: str
    confidence: float


class AnalysisState(TypedDict, total=False):
    domains: list[str]
    findings: Annotated[list[dict[str, Any]], operator.add]


async def test_each_send_branch_runs_its_own_subagent_and_findings_merge() -> None:
    scripts = {
        "fundamentals": [
            tool_call_message("Finding", {"summary": "quality balance", "confidence": 0.8}, "f")
        ],
        "technicals": [
            tool_call_message("Finding", {"summary": "trend strength", "confidence": 0.7}, "t")
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

    def plan(state: AnalysisState) -> dict[str, Any]:
        return {"domains": ["fundamentals", "technicals"]}

    def dispatch(state: AnalysisState) -> list[Send]:
        return [Send("specialist", {"domain": domain}) for domain in state["domains"]]

    async def specialist(task: dict[str, Any]) -> dict[str, Any]:
        domain = task["domain"]
        result = await specialists[domain].ainvoke(
            {"messages": [HumanMessage(f"analyze {domain}")]}
        )
        return {"findings": [result["structured_response"].model_dump()]}

    graph = StateGraph(AnalysisState)
    graph.add_node("plan", plan)
    graph.add_node("specialist", specialist)
    graph.add_node("synthesize", lambda state: {})
    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", dispatch, ["specialist"])
    graph.add_edge("specialist", "synthesize")
    graph.add_edge("synthesize", END)

    result = await graph.compile().ainvoke({})

    assert sorted(f["summary"] for f in result["findings"]) == [
        "quality balance",
        "trend strength",
    ]
