from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage
from tests.graphs.analysis.conftest import AS_OF

from fin_graph.agents.testing.scripted_model import ScriptedChatModel, tool_call_message
from fin_graph.config.settings import Settings
from fin_graph.domain.analysis import AnalystFinding, AnalystTask, ThesisHypothesis, TickerSignal
from fin_graph.domain.quant.common import Signal
from fin_graph.domain.requests.model import AnalysisRequest
from fin_graph.domain.tools import Domain
from fin_graph.domain.window import AnalysisWindow
from fin_graph.graphs.analysis.services import (
    LangChainAnalysisServices,
    ModelOutputError,
    SynthesisContext,
    UnavailableAnalysisServices,
    reconcile_signals,
    run_tool_plan,
)
from fin_graph.harness.runtime import analyst_mode
from fin_graph.integrations.analytics.tools import ANALYTICS_TOOLS
from fin_graph.integrations.context import RunContext
from fin_graph.llm.models import ModelRole, missing_credentials

REQUEST = AnalysisRequest(tickers=["DEEP"], as_of=AS_OF)
WINDOW = AnalysisWindow(as_of=AS_OF)
HYPOTHESES = [ThesisHypothesis(id="H1", statement="s", confirm_if="c", refute_if="r")]
GRAHAM = AnalystTask(domain=Domain.GRAHAM, objective="score")


def services(*responses: AIMessage, mode: str = "tool_plan") -> LangChainAnalysisServices:
    model = ScriptedChatModel(responses=list(responses))
    return LangChainAnalysisServices(dict.fromkeys(ModelRole, model), mode=mode)  # type: ignore[arg-type]


async def test_tool_plan_runs_the_compute_tool_for_every_ticker(context: RunContext) -> None:
    finding, evidence = await run_tool_plan(GRAHAM, REQUEST, WINDOW, ANALYTICS_TOOLS, context)

    assert [item.tool for item in evidence] == ["compute_graham"]
    assert finding.signal_for("DEEP").signal is Signal.BULLISH  # type: ignore[union-attr]
    assert finding.evidence_ids == [evidence[0].id] and finding.degraded


async def test_the_model_explains_but_never_changes_the_signal(context: RunContext) -> None:
    narrative = tool_call_message(
        "AnalystNarrative",
        {
            "summary": "Graham ve un descuento neto-neto.",
            "verdicts": [
                {
                    "hypothesis_id": "H1",
                    "verdict": "supports",
                    "reason": "NCAV",
                    "evidence_ids": ["ev_x"],
                },
                {"hypothesis_id": "H9", "verdict": "refutes", "reason": "?", "evidence_ids": []},
            ],
        },
        "call-1",
    )

    finding, _ = await services(narrative).analyze(
        GRAHAM, HYPOTHESES, REQUEST, WINDOW, ANALYTICS_TOOLS, context
    )

    assert finding.summary == "Graham ve un descuento neto-neto." and not finding.degraded
    assert [item.hypothesis_id for item in finding.verdicts] == ["H1"]
    assert finding.verdicts[0].evidence_ids == []  # an invented id is dropped
    assert finding.signal_for("DEEP").signal is Signal.BULLISH  # type: ignore[union-attr]


async def test_a_failed_narrative_keeps_the_score_finding(context: RunContext) -> None:
    finding, _ = await services(AIMessage("no json"), AIMessage("still none")).analyze(
        GRAHAM, HYPOTHESES, REQUEST, WINDOW, ANALYTICS_TOOLS, context
    )

    assert finding.degraded and finding.signals


async def test_a_failed_react_agent_falls_back_to_the_tool_plan(context: RunContext) -> None:
    finding, evidence = await services(mode="react").analyze(
        GRAHAM, HYPOTHESES, REQUEST, WINDOW, ANALYTICS_TOOLS, context
    )

    assert [item.tool for item in evidence] == ["compute_graham"] and finding.signals


def test_reconcile_replaces_contradicting_signals() -> None:
    from tests.domain.finance_helpers import evidence_for, scores_for

    evidence = list(evidence_for(scores_for("deep_value")).values())
    finding = AnalystFinding(
        domain=Domain.GRAHAM,
        summary="model",
        signals=[TickerSignal(ticker="DEEP", signal=Signal.BEARISH, confidence=99, rationale="?")],
        evidence_ids=["ev_unknown"],
        confidence=50,
    )

    fixed = reconcile_signals(finding, evidence)

    assert fixed.signal_for("DEEP").signal is Signal.BULLISH  # type: ignore[union-attr]
    assert fixed.signal_for("DEEP").rationale == ""  # type: ignore[union-attr]
    assert "ev_unknown" not in fixed.evidence_ids


async def test_tool_plan_skips_the_model_planner() -> None:
    plan = await services().plan(REQUEST, WINDOW, [Domain.GRAHAM, Domain.VALUATION], [])

    assert [task.domain for task in plan.tasks] == [Domain.VALUATION, Domain.GRAHAM]


async def test_the_narrative_keeps_score_stance_and_filters_claims(context: RunContext) -> None:
    findings_and_evidence = [
        await run_tool_plan(
            AnalystTask(domain=domain, objective="x"), REQUEST, WINDOW, ANALYTICS_TOOLS, context
        )
        for domain in (Domain.VALUATION, Domain.GRAHAM)
    ]
    findings = [item[0] for item in findings_and_evidence]
    evidence = [ev for item in findings_and_evidence for ev in item[1]]
    narrative = tool_call_message(
        "ThesisNarrative",
        {
            "summary": "Cotiza con margen de seguridad.",
            "bull_case": ["descuento"],
            "bear_case": ["trampa de valor"],
            "claims": [
                {
                    "statement": "El valor intrínseco supera el precio.",
                    "evidence_ids": [evidence[0].id],
                },
                {"statement": "Inventado.", "evidence_ids": ["ev_nope"]},
            ],
        },
        "call-1",
    )
    context_ = SynthesisContext(suggested_category="deep_value", suggested_stance="bullish")

    thesis = await services(narrative).synthesize(
        "DEEP", REQUEST, None, findings, evidence, context_
    )  # type: ignore[arg-type]

    assert thesis.stance is Signal.BULLISH and thesis.category == "deep_value"
    assert thesis.summary == "Cotiza con margen de seguridad." and not thesis.degraded
    model_claims = [claim for claim in thesis.claims if claim.source == "model"]
    assert [claim.statement for claim in model_claims] == ["El valor intrínseco supera el precio."]
    assert {claim.source for claim in thesis.claims} == {"scores", "model"}


async def test_unavailable_services_raise_for_every_model_step() -> None:
    unavailable = UnavailableAnalysisServices()

    with pytest.raises(ModelOutputError):
        await unavailable.extract("hola")
    with pytest.raises(ModelOutputError):
        await unavailable.plan(REQUEST, WINDOW, [], [])


def test_analyst_mode_and_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    assert analyst_mode(Settings(_env_file=None, llm_provider="ollama")) == "tool_plan"
    assert analyst_mode(Settings(_env_file=None, llm_provider="anthropic")) == "react"
    assert (
        analyst_mode(Settings(_env_file=None, fin_analyst_mode="react", llm_provider="ollama"))
        == "react"
    )
    assert missing_credentials("ollama") is None
    assert missing_credentials("anthropic") == "ANTHROPIC_API_KEY"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fixture")
    assert missing_credentials("anthropic") is None
