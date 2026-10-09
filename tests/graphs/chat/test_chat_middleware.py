from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import BaseTool, tool
from langgraph.store.memory import InMemoryStore

from sre_graph.agents.testing.scripted_model import ScriptedChatModel, tool_call_message
from sre_graph.config.settings import Settings
from sre_graph.domain.tools import Domain, EvidenceType, ToolMeta, attach_tool_meta
from sre_graph.graphs.chat.graph import make_chat_graph
from sre_graph.graphs.remediation.graph import ActionCatalog
from sre_graph.integrations.context import RunContext
from sre_graph.memory.service import IncidentMemory
from sre_graph.safety.approval import ApprovalPolicy


class EmptyRegistry:
    def tools(self, context: RunContext) -> list[object]:
        return []


def _chat(model: ScriptedChatModel, settings: Settings | None = None):
    return make_chat_graph(
        model,
        EmptyRegistry(),
        RunContext(),
        IncidentMemory(InMemoryStore()),
        ActionCatalog([]),
        policy=ApprovalPolicy(environment="dev"),
        settings=settings,
    )


async def test_chat_reconsiders_root_cause_claim_without_turn_evidence() -> None:
    model = ScriptedChatModel(
        responses=[
            AIMessage("La causa raíz fue un despliegue fallido."),
            AIMessage("No hay evidencia todavía; revisaría el despliegue como hipótesis."),
        ]
    )

    result = await _chat(model).ainvoke({"messages": [HumanMessage("¿Qué pasó?")]})

    assert "hipótesis" in result["messages"][-1].text
    assert len(model.calls) == 2


async def test_chat_uses_configured_fallback_on_primary_model_error(monkeypatch) -> None:
    primary = ScriptedChatModel(responses=[])
    backup = ScriptedChatModel(responses=[AIMessage("Puedo revisar la alerta.")])
    monkeypatch.setattr(
        "langchain.agents.middleware.model_fallback.init_chat_model",
        lambda model: backup,
    )
    settings = Settings(_env_file=None, llm_fallback_models=("openai:backup",))

    result = await _chat(primary, settings).ainvoke(
        {"messages": [HumanMessage("Revisa la alerta")]}
    )

    assert result["messages"][-1].text == "Puedo revisar la alerta."
    assert len(backup.calls) == 1


def test_fallback_models_accept_comma_separated_environment_setting(monkeypatch) -> None:
    monkeypatch.setenv("LLM_FALLBACK_MODELS", "openai:backup, anthropic:spare")

    settings = Settings(_env_file=None)

    assert settings.llm_fallback_models == ("openai:backup", "anthropic:spare")


async def test_specialist_masks_tool_results_and_restores_tool_arguments() -> None:
    inspected: list[str] = []

    @tool
    def discover_node() -> str:
        """Read the node address from the cluster."""
        return "Node address: 10.42.7.9"

    @tool
    def inspect_node(address: str) -> str:
        """Read the status of the given node address."""
        inspected.append(address)
        return f"Node {address} is healthy"

    meta = ToolMeta(
        integration="kubernetes",
        domains=frozenset({Domain.KUBERNETES}),
        evidence_type=EvidenceType.TOPOLOGY,
    )

    class Registry:
        def tools(self, context: RunContext) -> list[BaseTool]:
            return [attach_tool_meta(discover_node, meta), attach_tool_meta(inspect_node, meta)]

    model = ScriptedChatModel(
        responses=[
            tool_call_message("ask_kubernetes", {"task": "Inspect the node"}, "delegate"),
            tool_call_message("discover_node", {}, "discover"),
            tool_call_message("inspect_node", {"address": "<IP_ADDRESS_3e9fae93b2>"}, "inspect"),
            AIMessage("Node <IP_ADDRESS_3e9fae93b2> is healthy"),
            AIMessage("Node <IP_ADDRESS_3e9fae93b2> is healthy"),
        ]
    )
    settings = Settings(
        _env_file=None,
        mask_enabled=True,
        mask_kinds=("ip_address",),
        mask_key="chat-mask-test",
    )
    graph = make_chat_graph(
        model,
        Registry(),
        RunContext(),
        IncidentMemory(InMemoryStore()),
        ActionCatalog([]),
        policy=ApprovalPolicy(environment="dev"),
        settings=settings,
    )

    result = await graph.ainvoke({"messages": [HumanMessage("Inspect the node")]})

    assert "10.42.7.9" not in str(model.calls)
    assert "<IP_ADDRESS_3e9fae93b2>" in str(model.calls[2])
    assert inspected == ["10.42.7.9"]
    assert result["messages"][-1].text == "Node 10.42.7.9 is healthy"
