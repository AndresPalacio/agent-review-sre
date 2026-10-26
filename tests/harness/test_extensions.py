from __future__ import annotations

import pytest

from fin_graph.domain.investigation import SpecialistTask
from fin_graph.domain.tools import Domain
from fin_graph.integrations.kubernetes.tools import TOOLS


def test_tool_extension_can_reorder_and_describe_but_not_replace_execution(monkeypatch):
    from fin_graph.graphs.investigation.extensions import configured_tools
    from fin_graph.graphs.investigation.strategy import tools as policy

    task = SpecialistTask(domain=Domain.KUBERNETES, objective="Inspect pods")
    original = TOOLS[0]
    monkeypatch.setattr(
        policy,
        "prepare_tools",
        lambda task, tools: [original.model_copy(update={"description": "Look here first"})],
    )
    configured = configured_tools(task, TOOLS)
    assert configured[0].description == "Look here first"
    assert original.description != "Look here first"
    monkeypatch.setattr(
        policy,
        "prepare_tools",
        lambda task, tools: [original.model_copy(update={"name": "send_report"})],
    )
    with pytest.raises(ValueError, match="tool"):
        configured_tools(task, TOOLS)


def test_middleware_registration_and_skill_are_consumed():
    from fin_graph.graphs.investigation.extensions import configured_middleware, investigation_skill

    task = SpecialistTask(domain=Domain.KUBERNETES, objective="Inspect pods")
    assert configured_middleware(task)[0].name == "InvestigationDiscipline"
    assert "evidence" in investigation_skill()


async def test_planning_extension_is_consumed_by_real_service(monkeypatch):
    from fin_graph.agents.testing.scripted_model import ScriptedChatModel, tool_call_message
    from fin_graph.domain.alerts.parsing import parse_alert
    from fin_graph.domain.window import resolve_window
    from fin_graph.evals.scenarios import load_suite
    from fin_graph.graphs.investigation.services import LangChainServices
    from fin_graph.graphs.investigation.strategy import planning
    from fin_graph.llm.models import ModelRole

    model = ScriptedChatModel(
        responses=[tool_call_message("InvestigationPlan", {"hypotheses": [], "tasks": []}, "p")]
    )

    def refine(plan, alert, available):
        return plan.model_copy(
            update={
                "tasks": [SpecialistTask(domain=Domain.KUBERNETES, objective="Injected strategy")]
            }
        )

    monkeypatch.setattr(planning, "refine_plan", refine)
    scenario = load_suite("smoke")[0]
    result = await LangChainServices({role: model for role in ModelRole}).plan(
        parse_alert(scenario.alert),
        resolve_window(None, now=scenario.reference_time),
        [Domain.KUBERNETES],
        [],
    )
    assert result.tasks[0].objective == "Injected strategy"
