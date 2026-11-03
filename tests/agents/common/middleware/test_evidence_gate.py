from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from fin_graph.agents.common.factory import make_agent
from fin_graph.agents.common.middleware.disclaimer import DISCLAIMER, DisclaimerMiddleware
from fin_graph.agents.common.middleware.evidence_gate import EvidenceGateMiddleware
from fin_graph.agents.common.testing.scripted_model import ScriptedChatModel


def _agent(*responses: str, disclaimer: bool = False) -> tuple[object, ScriptedChatModel]:
    model = ScriptedChatModel(responses=[AIMessage(text) for text in responses])
    middleware = [EvidenceGateMiddleware(), *([DisclaimerMiddleware()] if disclaimer else [])]
    return make_agent(name="chat", model=model, middleware=middleware), model


def test_an_unsupported_recommendation_gets_one_nudge_before_answer() -> None:
    agent, model = _agent(
        "Te recomiendo comprar AAPL.",
        "Sin análisis de este turno solo puedo plantearlo como hipótesis: podría estar barata.",
    )

    result = agent.invoke({"messages": [HumanMessage("¿Compro Apple?")]})

    assert "hipótesis" in result["messages"][-1].text
    assert len(model.calls) == 2
    assert "evidencia" in str(model.calls[1][-1].content).lower()
    assert "recomiendo comprar" in str(model.calls[1][-2].content)


async def test_this_turns_analysis_supports_the_recommendation() -> None:
    agent, model = _agent("Según el análisis verificado, deberías comprar MSFT.")
    result = await agent.ainvoke(
        {
            "messages": [
                HumanMessage("Analiza MSFT"),
                ToolMessage(
                    content='{"MSFT": {"stance": "bullish", "verified": true}}',
                    tool_call_id="a1",
                    name="analyze",
                ),
            ]
        }
    )

    assert result["messages"][-1].text.startswith("Según el análisis")
    assert len(model.calls) == 1


async def test_previous_turn_evidence_does_not_support_a_new_ticker() -> None:
    agent, model = _agent(
        "You should buy TSLA.",
        "I have no analysis of TSLA in this turn; it is only a hypothesis.",
    )
    result = await agent.ainvoke(
        {
            "messages": [
                HumanMessage("Analyze NVDA"),
                ToolMessage(content="scores", tool_call_id="e1", name="compute_valuation"),
                AIMessage("NVDA looks expensive."),
                HumanMessage("And TSLA?"),
            ]
        }
    )

    assert "hypothesis" in result["messages"][-1].text
    assert len(model.calls) == 2


def test_playbooks_are_guidance_not_evidence() -> None:
    agent, model = _agent(
        "Es una buena compra: cumple el checklist de valor.",
        "El playbook solo da una lista de comprobación; falta el análisis.",
    )
    result = agent.invoke(
        {
            "messages": [
                HumanMessage("¿Es value?"),
                ToolMessage(content="checklist", tool_call_id="p1", name="load_playbook"),
            ]
        }
    )

    assert "falta el análisis" in result["messages"][-1].text
    assert len(model.calls) == 2


def test_a_price_target_is_refused_even_with_evidence() -> None:
    agent, model = _agent(
        "El precio objetivo es 250 USD.",
        "No doy precios objetivo; el análisis muestra un margen de seguridad del 20%.",
    )
    result = agent.invoke(
        {
            "messages": [
                HumanMessage("¿Hasta dónde sube?"),
                ToolMessage(content="scores", tool_call_id="a1", name="analyze"),
            ]
        }
    )

    assert "No doy precios objetivo" in result["messages"][-1].text
    assert len(model.calls) == 2


def test_a_conditional_hypothesis_is_not_retried() -> None:
    agent, model = _agent("Si se confirma el margen, podría ser una compra; es una hipótesis.")

    result = agent.invoke({"messages": [HumanMessage("¿Qué opinas de KO?")]})

    assert len(model.calls) == 1 and "hipótesis" in result["messages"][-1].text


def test_answers_about_trading_carry_the_disclaimer_once() -> None:
    agent, _ = _agent("Tu posición en AAPL es de 10 acciones.", disclaimer=True)
    plain, _ = _agent("Hola, ¿en qué te ayudo?", disclaimer=True)

    answer = agent.invoke({"messages": [HumanMessage("¿Qué tengo?")]})["messages"]
    greeting = plain.invoke({"messages": [HumanMessage("hola")]})["messages"]

    assert answer[-1].text.count(DISCLAIMER) == 1 and len(answer) == 2
    assert DISCLAIMER not in greeting[-1].text
