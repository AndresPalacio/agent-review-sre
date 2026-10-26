from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from langchain_core.tools import tool

from fin_graph.agents.testing.scripted_model import ScriptedChatModel, tool_call_message
from fin_graph.domain.alerts.parsing import text_alert
from fin_graph.domain.diagnosis import Claim, Diagnosis, IncidentStatus
from fin_graph.domain.evidence import evidence_id
from fin_graph.domain.investigation import InvestigationPlan, SpecialistTask
from fin_graph.domain.tools import Domain, EvidenceType, ToolMeta, attach_tool_meta
from fin_graph.domain.window import resolve_window
from fin_graph.graphs.investigation.services import LangChainServices
from fin_graph.integrations.context import RunContext
from fin_graph.llm.models import ModelRole


@tool(response_format="content_and_artifact")
def read_metric(query: str) -> tuple[str, dict[str, str]]:
    """Read an offline metric fixture."""
    return "cpu is 99%", {"value": "99%", "Authorization": "Bearer private-value"}


async def test_scripted_specialist_collects_citable_redacted_tool_evidence() -> None:
    candidate = attach_tool_meta(
        read_metric.model_copy(deep=True),
        ToolMeta(
            integration="fixture",
            domains=frozenset({Domain.METRICS}),
            evidence_type=EvidenceType.METRICS,
        ),
    )
    item_id = evidence_id("read_metric", {"query": "cpu"})
    model = ScriptedChatModel(
        responses=[
            tool_call_message("read_metric", {"query": "cpu"}, "read-1"),
            tool_call_message(
                "SpecialistFinding",
                {
                    "domain": "metrics",
                    "summary": "CPU saturated",
                    "confidence": 0.8,
                    "evidence_ids": [item_id],
                    "verdicts": [],
                    "gaps": [],
                },
                "finding-1",
            ),
        ]
    )
    services = LangChainServices(models={ModelRole.WORKER: model})
    alert = text_alert("API CPU high")
    window = resolve_window(None, now=datetime(2026, 10, 7, tzinfo=UTC))
    finding, evidence = await services.investigate(
        SpecialistTask(domain=Domain.METRICS, objective="check CPU", hypothesis_ids=["H1"]),
        alert,
        window,
        [candidate],
        RunContext(),
    )
    assert finding.summary == "CPU saturated"
    assert finding.evidence_ids == [item_id]
    assert len(evidence) == 1
    assert evidence[0].id == item_id
    assert evidence[0].integration == "fixture"
    assert evidence[0].data["Authorization"] != "Bearer private-value"
    assert "private-value" not in str(evidence[0].model_dump())


async def test_scripted_intake_extracts_free_text() -> None:
    model = ScriptedChatModel(
        responses=[
            tool_call_message(
                "AlertExtraction",
                {"is_noise": False, "alert_name": "API latency", "service": "checkout"},
                "extract-1",
            )
        ]
    )
    extracted = await LangChainServices(models={ModelRole.FAST: model}).extract(
        "checkout latency rose"
    )
    assert extracted.alert_name == "API latency"
    assert extracted.service == "checkout"


async def test_scripted_planner_synthesizer_and_judge_return_domain_models() -> None:
    reasoning = ScriptedChatModel(
        responses=[
            tool_call_message(
                "InvestigationPlan",
                {
                    "hypotheses": [
                        {
                            "id": "H1",
                            "statement": "CPU saturation",
                            "confirm_if": "CPU stays high",
                            "rule_out_if": "CPU is normal",
                        }
                    ],
                    "tasks": [
                        {"domain": "metrics", "objective": "check CPU", "hypothesis_ids": ["H1"]}
                    ],
                },
                "plan-1",
            ),
            tool_call_message(
                "Diagnosis",
                {
                    "triage_summary": "API slow",
                    "status": {
                        "confirmed": "latency high",
                        "open": "cause",
                        "next": "check CPU",
                        "owner": "on-call",
                    },
                    "root_cause": "Not yet known",
                    "category": "unknown",
                    "confidence": 0.2,
                },
                "diagnosis-1",
            ),
        ]
    )
    judge = ScriptedChatModel(
        responses=[
            tool_call_message(
                "ClaimJudgment", {"supported": False, "reason": "no matching evidence"}, "judge-1"
            )
        ]
    )
    services = LangChainServices(models={ModelRole.REASONING: reasoning, ModelRole.JUDGE: judge})
    alert = text_alert("API latency")
    window = resolve_window(None, now=datetime(2026, 10, 7, tzinfo=UTC))

    plan = await services.plan(alert, window, [Domain.METRICS], [])
    diagnosis = await services.synthesize(alert, plan, [], [])
    verdict = await services.judge(Claim(statement="CPU high", evidence_ids=[]), [])

    assert isinstance(plan, InvestigationPlan)
    assert plan.tasks[0].domain is Domain.METRICS
    assert diagnosis.category == "unknown"
    assert not verdict.supported


async def test_synthesizer_receives_bounded_reference_context_as_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = LangChainServices(models={})
    captured: dict[str, Any] = {}

    async def fake_structured(role: Any, schema: Any, prompt: str, payload: Any) -> Diagnosis:
        captured.update({"prompt": prompt, "payload": payload})
        return Diagnosis(
            triage_summary="Still investigating",
            status=IncidentStatus(confirmed="alert", open="cause", next="inspect", owner="on-call"),
            root_cause="unknown",
            category="unknown",
            confidence=0.1,
        )

    monkeypatch.setattr(services, "_structured", fake_structured)
    prior = [{"id": f"run-{index}", "summary": "prior note"} for index in range(4)]
    guides = [{"id": "kubernetes-rollout", "content": "Check rollout"}] * 3
    await services.synthesize_with_context(
        text_alert("API latency"), InvestigationPlan(hypotheses=[], tasks=[]), [], [], prior, guides
    )

    context = captured["payload"]["reference_context"]
    assert len(context["prior_incidents"]) == 3
    assert len(context["trusted_runbooks"]) == 2
    assert "never current-incident evidence" in captured["prompt"]
    assert captured["payload"]["evidence"] == []


async def test_specialist_stops_after_eight_tool_calls() -> None:
    candidate = attach_tool_meta(
        read_metric.model_copy(deep=True),
        ToolMeta(
            integration="fixture",
            domains=frozenset({Domain.METRICS}),
            evidence_type=EvidenceType.METRICS,
        ),
    )
    model = ScriptedChatModel(
        responses=[
            tool_call_message("read_metric", {"query": "cpu"}, f"read-{index}")
            for index in range(9)
        ]
    )
    services = LangChainServices(models={ModelRole.WORKER: model})
    with pytest.raises(Exception, match=r"limit|Limit|maximum"):
        await services.investigate(
            SpecialistTask(domain=Domain.METRICS, objective="check CPU"),
            text_alert("API CPU high"),
            resolve_window(None, now=datetime(2026, 10, 7, tzinfo=UTC)),
            [candidate],
            RunContext(),
        )
    assert len(model.calls) <= 9
