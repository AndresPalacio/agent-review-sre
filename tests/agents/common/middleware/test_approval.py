from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from fin_graph.agents.common.factory import make_agent
from fin_graph.agents.common.middleware.approval import approval_middleware
from fin_graph.agents.common.testing.scripted_model import ScriptedChatModel, tool_call_message
from fin_graph.domain.tools import Domain, EvidenceType, SideEffectLevel, ToolMeta, attach_tool_meta
from fin_graph.safety.approval import ApprovalPolicy

CONFIG: Any = {"configurable": {"thread_id": "t1"}}
PLACE_ORDER = tool_call_message("paper_place_order", {"ticker": "AAPL"}, "c1")


def _meta(level: SideEffectLevel) -> ToolMeta:
    return ToolMeta(
        integration="paper_broker",
        domains=frozenset({Domain.PORTFOLIO}),
        evidence_type=EvidenceType.POSITIONS,
        side_effect_level=level,
    )


def _tools(executed: list[str]) -> list[BaseTool]:
    @tool
    def portfolio_positions(account: str) -> str:
        """List paper portfolio positions."""
        executed.append(f"positions {account}")
        return "2 positions"

    @tool
    def paper_place_order(ticker: str) -> str:
        """Place a paper order for a ticker."""
        executed.append(f"order {ticker}")
        return "order placed"

    return [
        attach_tool_meta(portfolio_positions, _meta(SideEffectLevel.READ_ONLY)),
        attach_tool_meta(paper_place_order, _meta(SideEffectLevel.MUTATING)),
    ]


def _agent(
    policy: ApprovalPolicy, executed: list[str], call: AIMessage
) -> CompiledStateGraph[Any, Any, Any, Any]:
    tools = _tools(executed)
    return make_agent(
        name="operator",
        model=ScriptedChatModel(responses=[call, AIMessage("done")]),
        tools=tools,
        middleware=[approval_middleware(policy, tools)],
        checkpointer=InMemorySaver(),
    )


def test_a_mutating_call_pauses_with_the_standard_payload() -> None:
    executed: list[str] = []
    agent = _agent(ApprovalPolicy(), executed, PLACE_ORDER)

    paused = agent.invoke({"messages": [HumanMessage("buy AAPL in the paper account")]}, CONFIG)

    request = paused["__interrupt__"][0].value
    assert executed == []
    assert request["action_requests"][0]["name"] == "paper_place_order"
    assert request["action_requests"][0]["args"] == {"ticker": "AAPL"}
    assert request["review_configs"][0]["allowed_decisions"] == ["approve", "edit", "reject"]


def test_an_approved_call_runs_after_resume() -> None:
    executed: list[str] = []
    agent = _agent(ApprovalPolicy(), executed, PLACE_ORDER)
    agent.invoke({"messages": [HumanMessage("buy AAPL in the paper account")]}, CONFIG)

    agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), CONFIG)

    assert executed == ["order AAPL"]


def test_a_rejected_call_never_runs_and_the_model_is_told_why() -> None:
    executed: list[str] = []
    agent = _agent(ApprovalPolicy(), executed, PLACE_ORDER)
    agent.invoke({"messages": [HumanMessage("buy AAPL in the paper account")]}, CONFIG)

    result = agent.invoke(
        Command(resume={"decisions": [{"type": "reject", "message": "not during peak"}]}), CONFIG
    )

    assert executed == []
    rejection = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert "not during peak" in str(rejection.content)


def test_read_only_calls_run_without_pausing_even_in_production() -> None:
    executed: list[str] = []
    positions = tool_call_message("portfolio_positions", {"account": "paper-main"}, "c1")
    agent = _agent(ApprovalPolicy(environment="prod"), executed, positions)

    result = agent.invoke({"messages": [HumanMessage("show paper positions")]}, CONFIG)

    assert "__interrupt__" not in result
    assert executed == ["positions paper-main"]


def test_an_auto_approved_call_runs_without_pausing() -> None:
    executed: list[str] = []
    policy = ApprovalPolicy(environment="dev", auto_approve=frozenset({"paper_place_order"}))
    agent = _agent(policy, executed, PLACE_ORDER)

    result = agent.invoke({"messages": [HumanMessage("buy AAPL in the paper account")]}, CONFIG)

    assert "__interrupt__" not in result
    assert executed == ["order AAPL"]
