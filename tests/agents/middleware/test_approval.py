from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from sre_graph.agents.factory import make_agent
from sre_graph.agents.middleware.approval import approval_middleware
from sre_graph.agents.testing.scripted_model import ScriptedChatModel, tool_call_message
from sre_graph.domain.tools import Domain, EvidenceType, SideEffectLevel, ToolMeta, attach_tool_meta
from sre_graph.safety.approval import ApprovalPolicy

CONFIG: Any = {"configurable": {"thread_id": "t1"}}
UNDO_API = tool_call_message("k8s_rollout_undo", {"deployment": "api"}, "c1")


def _meta(level: SideEffectLevel) -> ToolMeta:
    return ToolMeta(
        integration="kubernetes",
        domains=frozenset({Domain.KUBERNETES}),
        evidence_type=EvidenceType.TOPOLOGY,
        side_effect_level=level,
    )


def _tools(executed: list[str]) -> list[BaseTool]:
    @tool
    def k8s_list_pods(namespace: str) -> str:
        """List pods."""
        executed.append(f"list {namespace}")
        return "3 pods"

    @tool
    def k8s_rollout_undo(deployment: str) -> str:
        """Roll a deployment back to its previous revision."""
        executed.append(f"undo {deployment}")
        return "rolled back"

    return [
        attach_tool_meta(k8s_list_pods, _meta(SideEffectLevel.READ_ONLY)),
        attach_tool_meta(k8s_rollout_undo, _meta(SideEffectLevel.MUTATING)),
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
    agent = _agent(ApprovalPolicy(), executed, UNDO_API)

    paused = agent.invoke({"messages": [HumanMessage("roll back api")]}, CONFIG)

    request = paused["__interrupt__"][0].value
    assert executed == []
    assert request["action_requests"][0]["name"] == "k8s_rollout_undo"
    assert request["action_requests"][0]["args"] == {"deployment": "api"}
    assert request["review_configs"][0]["allowed_decisions"] == ["approve", "edit", "reject"]


def test_an_approved_call_runs_after_resume() -> None:
    executed: list[str] = []
    agent = _agent(ApprovalPolicy(), executed, UNDO_API)
    agent.invoke({"messages": [HumanMessage("roll back api")]}, CONFIG)

    agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), CONFIG)

    assert executed == ["undo api"]


def test_a_rejected_call_never_runs_and_the_model_is_told_why() -> None:
    executed: list[str] = []
    agent = _agent(ApprovalPolicy(), executed, UNDO_API)
    agent.invoke({"messages": [HumanMessage("roll back api")]}, CONFIG)

    result = agent.invoke(
        Command(resume={"decisions": [{"type": "reject", "message": "not during peak"}]}), CONFIG
    )

    assert executed == []
    rejection = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert "not during peak" in str(rejection.content)


def test_read_only_calls_run_without_pausing_even_in_production() -> None:
    executed: list[str] = []
    list_pods = tool_call_message("k8s_list_pods", {"namespace": "payments"}, "c1")
    agent = _agent(ApprovalPolicy(environment="prod"), executed, list_pods)

    result = agent.invoke({"messages": [HumanMessage("list pods")]}, CONFIG)

    assert "__interrupt__" not in result
    assert executed == ["list payments"]


def test_an_auto_approved_call_runs_without_pausing() -> None:
    executed: list[str] = []
    policy = ApprovalPolicy(environment="dev", auto_approve=frozenset({"k8s_rollout_undo"}))
    agent = _agent(policy, executed, UNDO_API)

    result = agent.invoke({"messages": [HumanMessage("roll back api")]}, CONFIG)

    assert "__interrupt__" not in result
    assert executed == ["undo api"]
