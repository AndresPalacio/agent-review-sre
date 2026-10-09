from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from sre_graph.agents.factory import make_agent
from sre_graph.agents.middleware.evidence_gate import EvidenceGateMiddleware
from sre_graph.agents.testing.scripted_model import ScriptedChatModel


def _agent(*responses: str) -> tuple[object, ScriptedChatModel]:
    model = ScriptedChatModel(responses=[AIMessage(text) for text in responses])
    return make_agent(name="chat", model=model, middleware=[EvidenceGateMiddleware()]), model


def test_unsupported_root_cause_gets_one_nudge_before_answer() -> None:
    agent, model = _agent(
        "La causa raíz fue un límite de memoria bajo.",
        "No tengo evidencia de este turno; el límite de memoria es una hipótesis.",
    )

    result = agent.invoke({"messages": [HumanMessage("¿Cuál fue la causa?")]})

    assert result["messages"][-1].text.startswith("No tengo evidencia")
    assert len(model.calls) == 2
    assert "evidencia" in str(model.calls[1][-1].content).lower()
    assert "La causa raíz" in str(model.calls[1][-2].content)


async def test_async_gate_uses_current_turn_specialist_evidence() -> None:
    agent, model = _agent("La causa raíz fue un límite de memoria bajo.")
    result = await agent.ainvoke(
        {
            "messages": [
                HumanMessage("Analiza este incidente"),
                ToolMessage(
                    content="El pod terminó OOMKilled al alcanzar el límite de 512Mi",
                    tool_call_id="e1",
                    name="ask_kubernetes",
                ),
            ]
        }
    )

    assert result["messages"][-1].text == "La causa raíz fue un límite de memoria bajo."
    assert len(model.calls) == 1


async def test_previous_turn_evidence_does_not_justify_current_turn_cause() -> None:
    agent, model = _agent(
        "Root cause was a failed deployment.",
        "I cannot establish that cause from this turn's evidence.",
    )
    result = await agent.ainvoke(
        {
            "messages": [
                HumanMessage("Analyze incident A"),
                ToolMessage(content="rollout failed", tool_call_id="e1", name="investigate"),
                AIMessage("Incident A is understood."),
                HumanMessage("What caused incident B?"),
            ]
        }
    )

    assert "cannot establish" in result["messages"][-1].text
    assert len(model.calls) == 2


def test_runbook_guidance_is_not_incident_evidence() -> None:
    agent, model = _agent(
        "La causa raíz es una fuga de memoria.",
        "El runbook solo sugiere comprobar la memoria; falta evidencia.",
    )
    result = agent.invoke(
        {
            "messages": [
                HumanMessage("Diagnostica"),
                ToolMessage(content="Revisa memoria", tool_call_id="r1", name="load_runbook"),
            ]
        }
    )

    assert "falta evidencia" in result["messages"][-1].text
    assert len(model.calls) == 2


def test_hypothesis_without_causal_certainty_is_not_retried() -> None:
    agent, model = _agent("La fuga de memoria podría ser una hipótesis; faltan datos.")

    result = agent.invoke({"messages": [HumanMessage("¿Cuál es la causa?")]})

    assert "podría" in result["messages"][-1].text
    assert len(model.calls) == 1


def test_causado_por_claim_without_evidence_gets_nudge() -> None:
    agent, model = _agent(
        "El incidente fue causado por una consulta lenta.",
        "Una consulta lenta es una hipótesis aún sin comprobar.",
    )

    result = agent.invoke({"messages": [HumanMessage("¿Qué pasó?")]})

    assert "hipótesis" in result["messages"][-1].text
    assert len(model.calls) == 2
