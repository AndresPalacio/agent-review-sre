from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import BaseTool, tool
from langgraph.store.memory import InMemoryStore

from fin_graph.agents.testing.scripted_model import ScriptedChatModel, tool_call_message
from fin_graph.config.settings import Settings
from fin_graph.domain.tools import Domain, EvidenceType, ToolMeta, attach_tool_meta
from fin_graph.graphs.chat.graph import make_chat_graph
from fin_graph.integrations.context import RunContext
from fin_graph.memory.theses import ThesisMemory
from fin_graph.safety.approval import ApprovalPolicy
from fin_graph.safety.masking import Masker, MaskingPolicy


class EmptyRegistry:
    def tools(self, context: RunContext) -> list[object]:
        return []


def _chat(model: ScriptedChatModel, settings: Settings | None = None, registry: object = None):
    return make_chat_graph(
        model,
        registry or EmptyRegistry(),
        RunContext(),
        ThesisMemory(InMemoryStore()),
        policy=ApprovalPolicy(environment="dev"),
        settings=settings,
    )


async def test_chat_reconsiders_a_recommendation_without_turn_evidence() -> None:
    model = ScriptedChatModel(
        responses=[
            AIMessage("Deberías comprar NVDA."),
            AIMessage("Sin análisis de este turno solo es una hipótesis; podría estar cara."),
        ]
    )

    result = await _chat(model).ainvoke({"messages": [HumanMessage("¿Compro NVDA?")]})

    assert "hipótesis" in result["messages"][-1].text
    assert len(model.calls) == 2


async def test_chat_uses_configured_fallback_on_primary_model_error(monkeypatch) -> None:
    primary = ScriptedChatModel(responses=[])
    backup = ScriptedChatModel(responses=[AIMessage("Puedo revisar tu cartera.")])
    monkeypatch.setattr(
        "langchain.agents.middleware.model_fallback.init_chat_model",
        lambda model: backup,
    )
    settings = Settings(_env_file=None, llm_fallback_models=("openai:backup",))

    result = await _chat(primary, settings).ainvoke({"messages": [HumanMessage("Hola")]})

    assert result["messages"][-1].text.startswith("Puedo revisar tu cartera.")
    assert len(backup.calls) == 1


def test_fallback_models_accept_comma_separated_environment_setting(monkeypatch) -> None:
    monkeypatch.setenv("LLM_FALLBACK_MODELS", "openai:backup, anthropic:spare")

    settings = Settings(_env_file=None)

    assert settings.llm_fallback_models == ("openai:backup", "anthropic:spare")


async def test_analyst_subagent_masks_tool_results_and_restores_tool_arguments() -> None:
    contacted: list[str] = []
    email = "ir@deep.example"

    @tool
    def compute_fundamentals(ticker: str) -> str:
        """Score the fundamentals of a ticker."""
        return f"{ticker}: ROE 21%"

    @tool
    def investor_relations(ticker: str) -> str:
        """Read the investor relations contact of a ticker."""
        return f"Investor relations: {email}"

    @tool
    def request_filing(address: str) -> str:
        """Ask the given investor relations address for the latest filing."""
        contacted.append(address)
        return f"Filing requested from {address}"

    meta = ToolMeta(
        integration="analytics",
        domains=frozenset({Domain.FUNDAMENTALS}),
        evidence_type=EvidenceType.METRICS,
    )

    class Registry:
        def tools(self, context: RunContext) -> list[BaseTool]:
            return [
                attach_tool_meta(item, meta)
                for item in (compute_fundamentals, investor_relations, request_filing)
            ]

    settings = Settings(
        _env_file=None, mask_enabled=True, mask_kinds=("email",), mask_key="chat-mask-test"
    )
    token = Masker(MaskingPolicy.from_settings(settings)).mask(email)
    assert token != email
    model = ScriptedChatModel(
        responses=[
            tool_call_message(
                "task",
                {"description": "Get the DEEP filing", "subagent_type": "analyst_fundamentals"},
                "delegate",
            ),
            tool_call_message("investor_relations", {"ticker": "DEEP"}, "contact"),
            tool_call_message("request_filing", {"address": token}, "request"),
            AIMessage(f"Filing requested from {token}"),
            AIMessage(f"Pedí el informe a {token}."),
        ]
    )

    result = await _chat(model, settings, Registry()).ainvoke(
        {"messages": [HumanMessage("Pide el último informe de DEEP")]}
    )

    assert email not in str(model.calls)
    assert token in str(model.calls[2])
    assert contacted == [email]
    assert result["messages"][-1].text == f"Pedí el informe a {email}."
