from __future__ import annotations

import pytest

from fin_graph.domain.analysis import AnalystTask
from fin_graph.domain.tools import Domain
from fin_graph.integrations.analytics.tools import COMPUTE_TOOLS


def test_tool_extension_can_reorder_and_describe_but_not_replace_execution(monkeypatch):
    from fin_graph.graphs.analysis.extensions import configured_tools
    from fin_graph.graphs.analysis.strategy import tools as policy

    task = AnalystTask(domain=Domain.GRAHAM, objective="Inspect the margin of safety")
    original = COMPUTE_TOOLS[Domain.GRAHAM]
    monkeypatch.setattr(
        policy,
        "prepare_tools",
        lambda task, tools: [original.model_copy(update={"description": "Look here first"})],
    )
    configured = configured_tools(task, [original])
    assert configured[0].description == "Look here first"
    assert original.description != "Look here first"
    monkeypatch.setattr(
        policy,
        "prepare_tools",
        lambda task, tools: [original.model_copy(update={"name": "send_report"})],
    )
    with pytest.raises(ValueError, match="tool"):
        configured_tools(task, [original])


def test_middleware_registration_and_skill_are_consumed():
    from fin_graph.graphs.analysis.extensions import analysis_skill, configured_middleware

    task = AnalystTask(domain=Domain.GRAHAM, objective="Inspect the margin of safety")
    assert configured_middleware(task)[0].name == "AnalysisDiscipline"
    assert "evidence" in analysis_skill().lower()


async def test_planning_extension_is_consumed_by_real_service(monkeypatch):
    from fin_graph.agents.common.testing.scripted_model import ScriptedChatModel, tool_call_message
    from fin_graph.domain.requests.model import AnalysisRequest
    from fin_graph.domain.window import AnalysisWindow
    from fin_graph.evals.market.scenarios import load_equity_scenario
    from fin_graph.graphs.analysis.services import LangChainAnalysisServices
    from fin_graph.graphs.analysis.strategy import planning
    from fin_graph.llm.models import ModelRole

    model = ScriptedChatModel(
        responses=[tool_call_message("AnalysisPlan", {"hypotheses": [], "tasks": []}, "p")]
    )

    def refine(plan, request, available):
        return plan.model_copy(
            update={"tasks": [AnalystTask(domain=Domain.GRAHAM, objective="Injected strategy")]}
        )

    monkeypatch.setattr(planning, "refine_plan", refine)
    scenario = load_equity_scenario("001-deep-value")
    result = await LangChainAnalysisServices(
        {role: model for role in ModelRole}, mode="react"
    ).plan(
        AnalysisRequest.model_validate(scenario.request()),
        AnalysisWindow(as_of=scenario.as_of),
        [Domain.GRAHAM],
        [],
    )
    assert result.tasks[0].objective == "Injected strategy"
