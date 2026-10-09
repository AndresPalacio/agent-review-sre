from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from sre_graph.domain.alerts.model import NormalizedAlert
from sre_graph.domain.alerts.noise import AlertExtraction
from sre_graph.domain.diagnosis import Claim, Diagnosis, HypothesisOutcome, IncidentStatus
from sre_graph.domain.evidence import EvidenceItem
from sre_graph.domain.investigation import (
    Hypothesis,
    InvestigationPlan,
    SpecialistFinding,
    SpecialistTask,
    Verdict,
)
from sre_graph.domain.rendering.markdown import render_markdown
from sre_graph.domain.report import Report
from sre_graph.domain.tools import Domain, EvidenceType, ToolMeta, attach_tool_meta
from sre_graph.domain.window import IncidentWindow
from sre_graph.graphs.investigation.graph import InvestigationLimits, make_investigation_graph
from sre_graph.graphs.investigation.services import ClaimJudgment
from sre_graph.integrations.catalog import default_registry
from sre_graph.integrations.context import RunContext
from sre_graph.memory.service import IncidentMemory


@tool
def fixture_read(query: str) -> str:
    """Read an offline observation."""
    return query


class ToolSource:
    def __init__(self, domains: list[Domain]) -> None:
        self.candidates: list[BaseTool] = []
        for domain in domains:
            candidate = fixture_read.model_copy(deep=True)
            candidate.name = f"{domain.value}_read"
            self.candidates.append(
                attach_tool_meta(
                    candidate,
                    ToolMeta(
                        integration="fixture",
                        domains=frozenset({domain}),
                        evidence_type=EvidenceType.METRICS,
                    ),
                )
            )

    def tools(self, context: RunContext) -> list[BaseTool]:
        return self.candidates


class FakeServices:
    def __init__(self, *, fail: Domain | None = None, empty: bool = False) -> None:
        self.calls: list[str] = []
        self.fail = fail
        self.empty = empty
        self.windows: list[IncidentWindow] = []

    async def extract(self, text: str) -> AlertExtraction:
        self.calls.append("extract")
        return AlertExtraction(is_noise=False, alert_name="API CPU high")

    async def plan(
        self,
        alert: NormalizedAlert,
        window: IncidentWindow,
        available: list[Domain],
        findings: list[SpecialistFinding],
    ) -> InvestigationPlan:
        self.calls.append("plan")
        self.windows.append(window)
        return InvestigationPlan(
            hypotheses=[
                Hypothesis(
                    id="H1",
                    statement="CPU saturation",
                    confirm_if="CPU high",
                    rule_out_if="CPU normal",
                )
            ],
            tasks=[
                SpecialistTask(domain=domain, objective="check", hypothesis_ids=["H1"])
                for domain in available
            ],
        )

    async def investigate(
        self,
        task: SpecialistTask,
        alert: NormalizedAlert,
        window: IncidentWindow,
        tools: list[BaseTool],
        context: RunContext,
    ) -> tuple[SpecialistFinding, list[EvidenceItem]]:
        self.calls.append(task.domain.value)
        if task.domain == self.fail:
            raise RuntimeError("private-provider-detail")
        item_id = f"ev_{task.domain.value}"
        return (
            SpecialistFinding(
                domain=task.domain,
                summary=f"{task.domain.value} observed",
                evidence_ids=[] if self.empty else [item_id],
                confidence=0.0 if self.empty else 0.8,
            ),
            []
            if self.empty
            else [
                EvidenceItem(
                    id=item_id,
                    integration="fixture",
                    tool=tools[0].name,
                    evidence_type=EvidenceType.METRICS,
                    domains=[task.domain],
                    summary="CPU 99%",
                    collected_at=datetime(2026, 10, 7, tzinfo=UTC),
                )
            ],
        )

    async def synthesize(
        self,
        alert: NormalizedAlert,
        plan: InvestigationPlan,
        findings: list[SpecialistFinding],
        evidence: list[EvidenceItem],
    ) -> Diagnosis:
        self.calls.append("synthesize")
        ids = [item.id for item in evidence]
        return Diagnosis(
            triage_summary="API is slow",
            status=IncidentStatus(
                confirmed="Latency is high",
                open="Recovery time",
                next="Inspect CPU",
                owner="on-call",
            ),
            hypotheses=[
                HypothesisOutcome(
                    hypothesis_id="H1",
                    statement="CPU saturation",
                    verdict=Verdict.SUPPORTS if ids else Verdict.INCONCLUSIVE,
                    reason="CPU metric" if ids else "No observations",
                )
            ],
            verification=["CPU inspected"],
            follow_up_questions=["When did latency recover?"],
            remediation_tradeoffs="Scale versus reduce load",
            root_cause="CPU saturation" if ids else "Root cause unknown",
            category="cpu_saturation" if ids else "unknown",
            causal_chain=["CPU high", "latency high"] if ids else [],
            claims=[Claim(statement="CPU high", evidence_ids=ids[:1])] if ids else [],
            remediation_steps=["Reduce CPU load"] if ids else [],
            confidence=0.8 if ids else 0.1,
        )

    async def judge(self, claim: Claim, evidence: list[EvidenceItem]) -> ClaimJudgment:
        self.calls.append("judge")
        return ClaimJudgment(supported=True, reason="matches metric")


class FakeDelivery:
    def __init__(self) -> None:
        self.sent: list[tuple[dict[str, str], str]] = []

    async def send_report(
        self, report: Any, destinations: dict[str, str], idempotency_key: str
    ) -> list[Any]:
        self.sent.append((destinations, idempotency_key))
        return []


NOW = datetime(2026, 10, 7, tzinfo=UTC)
RAW = {"title": "API CPU high", "service": "checkout"}


async def test_progress_exposes_safe_plan_and_specialist_metadata() -> None:
    graph = make_investigation_graph(
        FakeServices(),
        ToolSource([Domain.METRICS]),
        clock=lambda: NOW,
        limits=InvestigationLimits(max_waves=1),
    )
    events = [
        event
        async for mode, event in graph.astream(
            {"raw_alert": RAW}, context=RunContext(), stream_mode=["custom"]
        )
        if mode == "custom"
    ]
    plan = next(item for item in events if item["type"] == "plan")
    specialist = next(item for item in events if item["type"] == "specialist")
    assert plan["data"] == {
        "stage": "plan",
        "wave": 1,
        "tasks": [{"domain": "metrics"}],
    }
    assert specialist["data"] == {
        "stage": "metrics",
        "wave": 1,
        "domain": "metrics",
        "evidence_ids": ["ev_metrics"],
    }
    assert "objective" not in json.dumps(events)


async def test_verified_report_is_saved_as_compact_incident_memory() -> None:
    store = InMemoryStore()
    memory = IncidentMemory(store)
    graph = make_investigation_graph(
        FakeServices(), ToolSource([Domain.METRICS]), clock=lambda: NOW, memory=memory
    )

    raw = {**RAW, "labels": {"service": "checkout"}}
    result = await graph.ainvoke({"raw_alert": raw, "run_id": "run-1"}, context=RunContext())
    incident = await store.aget(("org", "incidents"), "run-1")

    assert incident is not None
    assert incident.value["service"] == "checkout"
    assert incident.value["fingerprint"] == result["alert"]["fingerprint"]
    assert incident.value["evidence_ids"] == ["ev_metrics"]
    assert "messages" not in incident.value


async def test_context_reaches_synthesis_and_repeated_alerts_keep_both_reports() -> None:
    class ContextServices(FakeServices):
        def __init__(self) -> None:
            super().__init__()
            self.contexts: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []

        async def synthesize_with_context(
            self,
            alert: NormalizedAlert,
            plan: InvestigationPlan,
            findings: list[SpecialistFinding],
            evidence: list[EvidenceItem],
            recalled: list[dict[str, Any]],
            runbooks: list[dict[str, Any]],
        ) -> Diagnosis:
            self.contexts.append((recalled, runbooks))
            return await super().synthesize(alert, plan, findings, evidence)

    store = InMemoryStore()
    memory = IncidentMemory(store)
    services = ContextServices()
    graph = make_investigation_graph(
        services, ToolSource([Domain.METRICS]), clock=lambda: NOW, memory=memory
    )
    raw = {**RAW, "labels": {"service": "checkout", "category": "deployment_rollout_stuck"}}
    first = await graph.ainvoke({"raw_alert": raw, "run_id": "run-1"}, context=RunContext())
    second = await graph.ainvoke({"raw_alert": raw, "run_id": "run-2"}, context=RunContext())

    assert first["recalled"] == []
    assert second["recalled"][0]["id"] == "run-1"
    assert second["runbooks"][0]["id"] == "kubernetes-rollout"
    assert services.contexts[-1] == (second["recalled"], second["runbooks"])
    assert len(await store.asearch(("org", "incidents"))) == 2


async def test_noise_short_circuits_without_models_or_tools() -> None:
    services = FakeServices()
    graph = make_investigation_graph(services, ToolSource([Domain.METRICS]), clock=lambda: NOW)
    result = await graph.ainvoke({"raw_alert": "hi"}, context=RunContext())
    assert result["is_noise"] is True
    assert services.calls == []
    assert not result.get("report")


async def test_parallel_tasks_merge_findings_evidence_and_render_cited_report() -> None:
    services = FakeServices()
    graph = make_investigation_graph(
        services, ToolSource([Domain.METRICS, Domain.LOGS]), clock=lambda: NOW
    )
    result = await graph.ainvoke({"raw_alert": RAW}, context=RunContext())
    assert sorted(item["domain"] for item in result["findings"]) == ["logs", "metrics"]
    assert sorted(item["id"] for item in result["evidence"]) == ["ev_logs", "ev_metrics"]
    assert result["verification"]["passed"] is True
    assert result["report"]["labels"]
    assert services.calls.count("metrics") == services.calls.count("logs") == 1
    assert "E1" in render_markdown(Report.model_validate(result["report"]))
    json.dumps(result)


async def test_specialist_failure_is_isolated_and_secret_not_persisted() -> None:
    services = FakeServices(fail=Domain.LOGS)
    graph = make_investigation_graph(
        services, ToolSource([Domain.METRICS, Domain.LOGS]), clock=lambda: NOW
    )
    result = await graph.ainvoke({"raw_alert": RAW}, context=RunContext())
    assert len(result["findings"]) == 2
    assert any(item["confidence"] == 0.0 for item in result["findings"])
    assert "private-provider-detail" not in str(result)
    assert result["report"]


async def test_no_integrations_reports_unknown_without_model_guessing() -> None:
    services = FakeServices()
    graph = make_investigation_graph(services, ToolSource([]), clock=lambda: NOW)
    result = await graph.ainvoke({"raw_alert": RAW}, context=RunContext())
    assert result["diagnosis"]["category"] == "unknown"
    assert "plan" not in services.calls
    assert "synthesize" not in services.calls
    assert result["report"]


async def test_studio_invoke_without_runtime_context_returns_unknown() -> None:
    graph = make_investigation_graph(FakeServices(), ToolSource([]), clock=lambda: NOW)
    result = await graph.ainvoke({"raw_alert": RAW})
    assert result["diagnosis"]["category"] == "unknown"


async def test_studio_with_real_registry_builds_context_when_none_is_passed(
    monkeypatch: Any,
) -> None:
    registry = default_registry(env_file=None)
    monkeypatch.setattr(registry, "run_context", RunContext)
    graph = make_investigation_graph(FakeServices(), registry, clock=lambda: NOW)
    result = await graph.ainvoke({"raw_alert": RAW})
    assert result["diagnosis"]["category"] == "unknown"


@pytest.mark.parametrize(
    "raw",
    [
        {"title": "Failure password=test-secret-123", "token": "other-secret"},
        '{"title":"Failure password=test-secret-123","token":"other-secret"}',
    ],
)
async def test_raw_alert_secret_is_redacted_before_entering_graph_state(raw: Any) -> None:
    saver = InMemorySaver()
    graph = make_investigation_graph(
        FakeServices(), ToolSource([]), clock=lambda: NOW, checkpointer=saver
    )
    config = {"configurable": {"thread_id": "thread-secret"}}
    result = await graph.ainvoke(
        {"raw_alert": raw},
        config=config,
        context=RunContext(),
    )
    serialized = json.dumps(result)
    assert "test-secret-123" not in serialized
    assert "other-secret" not in serialized
    assert "[REDACTED:" in serialized
    history = json.dumps([item.checkpoint for item in saver.list(config)], default=str)
    assert "test-secret-123" not in history
    assert "other-secret" not in history
    assert "test-secret-123" not in str(saver.writes)
    assert "other-secret" not in str(saver.writes)


async def test_reused_thread_cannot_reuse_evidence_or_delivery_destinations() -> None:
    source = ToolSource([Domain.METRICS])
    delivery = FakeDelivery()
    compiled = make_investigation_graph(
        FakeServices(), source, delivery=delivery, clock=lambda: NOW, checkpointer=InMemorySaver()
    )
    config = {"configurable": {"thread_id": "thread-reused"}}
    first = await compiled.ainvoke(
        {"raw_alert": RAW, "destinations": {"slack": "C-test"}},
        config=config,
        context=RunContext(),
    )
    assert first["evidence"]
    assert len(delivery.sent) == 1

    source.candidates.clear()
    second = await compiled.ainvoke(
        {"raw_alert": {"title": "New incident", "service": "payments"}},
        config=config,
        context=RunContext(),
    )
    assert second["diagnosis"]["category"] == "unknown"
    assert second["evidence"] == []
    assert second["findings"] == []
    assert second["deliveries"] == []
    assert len(delivery.sent) == 1


async def test_noise_on_reused_thread_clears_prior_report() -> None:
    compiled = make_investigation_graph(
        FakeServices(),
        ToolSource([Domain.METRICS]),
        clock=lambda: NOW,
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "thread-noise"}}
    first = await compiled.ainvoke(
        {"raw_alert": RAW, "user_requested": True}, config=config, context=RunContext()
    )
    assert first["report"]
    second = await compiled.ainvoke({"raw_alert": "hi"}, config=config, context=RunContext())
    assert second["is_noise"] is True
    assert not second.get("report")


async def test_refinement_stops_at_three_waves_and_expands_empty_window() -> None:
    services = FakeServices(empty=True)
    graph = make_investigation_graph(
        services,
        ToolSource([Domain.METRICS]),
        clock=lambda: NOW,
        limits=InvestigationLimits(max_waves=3),
    )
    result = await graph.ainvoke({"raw_alert": RAW}, context=RunContext())
    assert result["wave"] == 3
    assert services.calls.count("metrics") == 3
    assert services.windows[1].lookback > services.windows[0].lookback
    assert result["report"]


async def test_failed_final_verification_cannot_publish_a_confident_root_cause() -> None:
    class RejectingJudge(FakeServices):
        async def judge(self, claim: Claim, evidence: list[EvidenceItem]) -> ClaimJudgment:
            return ClaimJudgment(supported=False, reason="observation does not prove cause")

    graph = make_investigation_graph(
        RejectingJudge(),
        ToolSource([Domain.METRICS]),
        clock=lambda: NOW,
        limits=InvestigationLimits(max_waves=1),
    )
    result = await graph.ainvoke({"raw_alert": RAW}, context=RunContext())
    assert result["verification"]["passed"] is False
    assert result["diagnosis"]["category"] == "unknown"
    assert result["diagnosis"]["confidence"] < 0.5
    assert "Reduce CPU load" not in str(result["report"])


@pytest.mark.parametrize("judgment", [False, True, None])
async def test_weak_claim_needs_a_successful_judge_before_publishing(judgment: bool | None) -> None:
    class WeakClaimServices(FakeServices):
        async def investigate(self, *args: Any, **kwargs: Any) -> Any:
            finding, evidence = await super().investigate(*args, **kwargs)
            observation = "CPU utilization is 99%" if judgment else "Application started"
            item = evidence[0].model_copy(
                update={
                    "evidence_type": EvidenceType.LOGS,
                    "summary": observation,
                    "data": observation,
                }
            )
            return finding, [item]

        async def synthesize(self, *args: Any, **kwargs: Any) -> Diagnosis:
            diagnosis = await super().synthesize(*args, **kwargs)
            return diagnosis.model_copy(update={"confidence": 1.0})

        async def judge(self, claim: Claim, evidence: list[EvidenceItem]) -> ClaimJudgment:
            self.calls.append("judge")
            if judgment is None:
                raise RuntimeError("judge unavailable")
            return ClaimJudgment(supported=judgment, reason="semantic judgment of cited logs")

    services = WeakClaimServices()
    graph = make_investigation_graph(
        services,
        ToolSource([Domain.METRICS]),
        clock=lambda: NOW,
        limits=InvestigationLimits(max_waves=1),
    )

    result = await graph.ainvoke({"raw_alert": RAW}, context=RunContext())

    assert services.calls.count("judge") == 1
    assert result["verification"]["passed"] is (judgment is True)
    assert result["verification"]["claims"][0]["status"] == (
        "supported" if judgment else "unsupported"
    )
    assert result["diagnosis"]["category"] == ("cpu_saturation" if judgment else "unknown")


@pytest.mark.parametrize(
    ("data", "expands"),
    [
        ([], True),
        ({}, True),
        ("", True),
        ({"status": "success", "data": {"resultType": "matrix", "result": []}}, True),
        (
            {
                "MetricDataResults": [
                    {"Id": "cpu", "Timestamps": [], "Values": [], "StatusCode": "Complete"}
                ]
            },
            True,
        ),
        ({"MetricList": [{"Key": {"Metric": "db.load.avg"}, "DataPoints": []}]}, True),
        ({"result": [{"metric": {"__name__": "cpu"}, "values": [[1, "0"]]}]}, False),
        ({"MetricDataResults": [{"Id": "cpu", "Timestamps": [1], "Values": [0]}]}, False),
        ({"healthy": False}, False),
    ],
)
async def test_empty_tool_observations_expand_each_refinement_window(
    data: Any, expands: bool
) -> None:
    class EmptyResults(FakeServices):
        async def investigate(self, *args: Any, **kwargs: Any) -> Any:
            finding, evidence = await super().investigate(*args, **kwargs)
            return finding, [evidence[0].model_copy(update={"data": data, "summary": str(data)})]

        async def judge(self, claim: Claim, evidence: list[EvidenceItem]) -> ClaimJudgment:
            return ClaimJudgment(supported=False, reason="No observations returned")

    services = EmptyResults()
    graph = make_investigation_graph(
        services,
        ToolSource([Domain.METRICS]),
        clock=lambda: NOW,
        limits=InvestigationLimits(max_waves=3),
    )

    result = await graph.ainvoke({"raw_alert": RAW}, context=RunContext())

    expected_minutes = [120, 240, 480] if expands else [120, 120, 120]
    assert [window.lookback for window in services.windows] == [
        timedelta(minutes=minutes) for minutes in expected_minutes
    ]
    assert result["wave"] == 3
    assert result["diagnosis"]["category"] == "unknown"


@pytest.mark.parametrize("has_data", [False, True])
@pytest.mark.parametrize(
    "source", ["kubernetes_logs", "tempo_search", "tempo_trace", "cloudwatch_logs", "aws_alarms"]
)
async def test_refinement_ignores_empty_read_envelopes_and_query_handles(
    source: str, has_data: bool
) -> None:
    reads: list[tuple[str, Any]]
    if source == "kubernetes_logs":
        reads = [
            (
                "k8s_pod_logs",
                {
                    "namespace": "default",
                    "pod": "app",
                    "container": None,
                    "previous": False,
                    "text": "0" if has_data else "",
                },
            )
        ]
    elif source == "tempo_search":
        reads = [
            (
                "tempo_search",
                {
                    "traces": [{"traceID": "a" * 32}] if has_data else [],
                    "metrics": {"inspectedTraces": 1},
                },
            )
        ]
    elif source == "tempo_trace":
        reads = [
            (
                "tempo_trace",
                {
                    "resourceSpans": [
                        {"scopeSpans": [{"spans": [{"traceId": "a" * 32, "spanId": "b" * 16}]}]}
                    ]
                    if has_data
                    else [],
                },
            )
        ]
    elif source == "aws_alarms":
        reads = [
            (
                "aws_alarms",
                {
                    "MetricAlarms": [{"AlarmName": "cpu", "StateValue": "OK"}] if has_data else [],
                    "CompositeAlarms": [],
                },
            )
        ]
    else:
        reads = [
            ("aws_logs_start", {"queryId": "query-1"}),
            (
                "aws_logs_results",
                {
                    "status": "Complete",
                    "results": [[{"field": "@message", "value": "0"}]] if has_data else [],
                },
            ),
        ]

    class RecordedReads(FakeServices):
        async def investigate(self, *args: Any, **kwargs: Any) -> Any:
            finding, evidence = await super().investigate(*args, **kwargs)
            return finding, [
                evidence[0].model_copy(
                    update={"id": name, "tool": name, "data": data, "summary": str(data)}
                )
                for name, data in reads
            ]

        async def judge(self, claim: Claim, evidence: list[EvidenceItem]) -> ClaimJudgment:
            return ClaimJudgment(supported=False, reason="Cause remains unverified")

    services = RecordedReads()
    graph = make_investigation_graph(
        services,
        ToolSource([Domain.LOGS]),
        clock=lambda: NOW,
        limits=InvestigationLimits(max_waves=3),
    )

    result = await graph.ainvoke({"raw_alert": RAW}, context=RunContext())

    expected_minutes = [120, 120, 120] if has_data else [120, 240, 480]
    assert [window.lookback for window in services.windows] == [
        timedelta(minutes=minutes) for minutes in expected_minutes
    ]
    assert result["wave"] == 3


async def test_expired_run_degrades_specialist_and_still_reports() -> None:
    moment = {"now": NOW}

    class AdvancingServices(FakeServices):
        async def plan(
            self,
            alert: NormalizedAlert,
            window: IncidentWindow,
            available: list[Domain],
            findings: list[SpecialistFinding],
        ) -> InvestigationPlan:
            planned = await super().plan(alert, window, available, findings)
            moment["now"] = NOW + timedelta(seconds=181)
            return planned

    services = AdvancingServices()
    graph = make_investigation_graph(
        services, ToolSource([Domain.METRICS]), clock=lambda: moment["now"]
    )
    result = await graph.ainvoke({"raw_alert": RAW}, context=RunContext())
    assert "metrics" not in services.calls
    assert result["findings"][0]["confidence"] == 0.0
    assert result["report"]


async def test_deadline_bounds_text_extraction_before_specialists() -> None:
    class SlowExtraction(FakeServices):
        async def extract(self, text: str) -> AlertExtraction:
            await asyncio.sleep(1.3)
            return await super().extract(text)

    graph = make_investigation_graph(
        SlowExtraction(), ToolSource([]), limits=InvestigationLimits(deadline_seconds=1)
    )
    start = time.monotonic()
    result = await graph.ainvoke({"raw_alert": "checkout reports 5xx"}, context=RunContext())
    elapsed = time.monotonic() - start
    assert elapsed < 1.2
    assert result["diagnosis"]["category"] == "unknown"
    assert any("TimeoutError" in item for item in result["errors"])


async def test_stream_contains_progress_events() -> None:
    graph = make_investigation_graph(
        FakeServices(), ToolSource([Domain.METRICS]), clock=lambda: NOW
    )
    events = [
        event
        async for event in graph.astream(
            {"raw_alert": RAW, "thread_id": "thread-1", "run_id": "run-1"},
            context=RunContext(),
            stream_mode="custom",
        )
    ]
    assert [event["data"]["stage"] for event in events][:3] == [
        "intake",
        "gather_context",
        "plan",
    ]
    assert all(event["thread_id"] == "thread-1" for event in events)


async def test_delivery_requires_explicit_destination() -> None:
    delivery = FakeDelivery()
    graph = make_investigation_graph(
        FakeServices(), ToolSource([Domain.METRICS]), delivery=delivery, clock=lambda: NOW
    )
    await graph.ainvoke({"raw_alert": RAW}, context=RunContext())
    assert delivery.sent == []
    await graph.ainvoke(
        {"raw_alert": RAW, "destinations": {"slack": "C-test"}}, context=RunContext()
    )
    assert delivery.sent[0][0] == {"slack": "C-test"}
