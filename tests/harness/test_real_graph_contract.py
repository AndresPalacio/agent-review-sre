"""Production analysis extensions and the financial graph contract work end to end."""

from __future__ import annotations

from fin_graph.agents.common.testing.scripted_model import ScriptedChatModel, tool_call_message
from fin_graph.domain.analysis import AnalystTask, ThesisHypothesis
from fin_graph.domain.requests.model import AnalysisRequest
from fin_graph.domain.tools import Domain
from fin_graph.domain.window import AnalysisWindow
from fin_graph.evals.market.runner import run_context, run_equity_suite
from fin_graph.evals.market.scenarios import load_equity_scenario
from fin_graph.integrations.analytics.tools import ANALYTICS_TOOLS
from fin_graph.llm.models import ModelRole
from fin_graph.workflows.analysis.services import LangChainAnalysisServices


async def test_real_agent_tools_middleware_skill_and_grader_work_together(tmp_path, monkeypatch):
    from langchain.agents.middleware import AgentMiddleware

    from fin_graph.harness.quality import assess_record
    from fin_graph.workflows.analysis.extensions import analysis_skill
    from fin_graph.workflows.analysis.strategy import registration

    middleware_calls = []

    class ObservedMiddleware(AgentMiddleware):
        async def awrap_model_call(self, request, handler):
            middleware_calls.append("middleware")
            return await handler(request)

    monkeypatch.setattr(registration, "build_middleware", lambda task: [ObservedMiddleware()])
    scenario = load_equity_scenario("002-quality-compounder")
    request = AnalysisRequest.model_validate(scenario.request())
    task = AnalystTask(
        domain=Domain.BUFFETT,
        objective="Score durable quality and owner earnings.",
        tickers=scenario.tickers,
        hypothesis_ids=["H1"],
    )
    hypothesis = ThesisHypothesis(
        id="H1",
        statement="MOAT compounds capital at attractive rates.",
        confirm_if="Returns and owner earnings are durable.",
        refute_if="Returns deteriorate or valuation overwhelms quality.",
    )
    tool_args = {"ticker": "MOAT", "as_of": scenario.as_of.isoformat()}
    model = ScriptedChatModel(
        responses=[
            tool_call_message("compute_buffett", tool_args, "score"),
            tool_call_message(
                "AnalystFinding",
                {
                    "domain": "buffett",
                    "summary": "The quality score supports the compounding hypothesis.",
                    "signals": [],
                    "confidence": 90,
                },
                "finding",
            ),
        ]
    )
    service = LangChainAnalysisServices(
        {role: model for role in ModelRole},
        mode="react",
    )

    finding, evidence = await service.analyze(
        task,
        [hypothesis],
        request,
        AnalysisWindow(as_of=scenario.as_of),
        ANALYTICS_TOOLS,
        run_context(scenario),
    )

    assert [item.tool for item in evidence] == ["compute_buffett"]
    assert finding.signal_for("MOAT") is not None
    assert middleware_calls == ["middleware", "middleware"]
    assert any(analysis_skill() in str(message.content) for call in model.calls for message in call)

    result = await run_equity_suite("contract-test", "scripted", 1, tmp_path, scenarios=[scenario])
    record = result["runs"][0]
    assert record["status"] == "completed", record
    assert assess_record(record) == [], record
    assert record["trajectory"]["passed"] is True
    agents = {event["name"] for event in record["trace"] if event["kind"] == "agent_start"}
    assert {
        "planner",
        "analyst_fundamentals",
        "analyst_valuation",
        "analyst_technicals",
        "analyst_sentiment",
        "analyst_buffett",
        "analyst_graham",
        "synthesizer",
    } <= agents
