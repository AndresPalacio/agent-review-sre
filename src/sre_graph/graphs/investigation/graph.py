"""Deterministic LangGraph spine for parallel, bounded incident investigation."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import Overwrite, Send

from sre_graph.domain.alerts.model import NormalizedAlert
from sre_graph.domain.alerts.noise import alert_from_extraction, is_trivial_message
from sre_graph.domain.alerts.parsing import parse_alert
from sre_graph.domain.diagnosis import Diagnosis, unknown_diagnosis
from sre_graph.domain.evidence import EvidenceItem
from sre_graph.domain.investigation import (
    InvestigationPlan,
    SpecialistFinding,
    SpecialistTask,
    constrain_plan,
    degraded_finding,
    fallback_plan,
)
from sre_graph.domain.remediation import RemediationAction, template_steps
from sre_graph.domain.report import Report, build_report
from sre_graph.domain.taxonomy import UNKNOWN
from sre_graph.domain.tools import Domain, SideEffectLevel, read_tool_meta
from sre_graph.domain.verification import ClaimStatus, VerificationReport, verify_diagnosis
from sre_graph.domain.window import IncidentWindow, resolve_window
from sre_graph.graphs.investigation.services import InvestigationServices
from sre_graph.graphs.investigation.specialists import available_specialists, read_tools
from sre_graph.graphs.investigation.state import InvestigationState, SanitizedInputChannel
from sre_graph.integrations.context import RunContext
from sre_graph.memory.service import IncidentMemory
from sre_graph.runbooks.catalog import select_runbooks
from sre_graph.safety.secrets import redact_secrets


class ToolSource(Protocol):
    def tools(self, context: RunContext) -> list[BaseTool]: ...


class ReportDelivery(Protocol):
    async def send_report(
        self, report: Report, destinations: dict[str, str], idempotency_key: str
    ) -> list[Any]: ...


@dataclass(frozen=True)
class InvestigationLimits:
    max_waves: int = 3
    max_tasks_per_wave: int = 5
    max_total_tasks: int = 15
    deadline_seconds: int = 180


DEFAULT_LIMITS = InvestigationLimits()


def make_investigation_graph(
    services: InvestigationServices,
    registry: ToolSource,
    *,
    delivery: ReportDelivery | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    limits: InvestigationLimits = DEFAULT_LIMITS,
    checkpointer: BaseCheckpointSaver[Any] | Literal[False] | None = None,
    memory: IncidentMemory | None = None,
    replay_intake: tuple[NormalizedAlert, IncidentWindow] | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile with optional trusted replay intake, never read from public graph state."""
    if (
        min(
            limits.max_waves,
            limits.max_tasks_per_wave,
            limits.max_total_tasks,
            limits.deadline_seconds,
        )
        < 1
    ):
        raise ValueError("investigation limits must be positive")

    async def intake(state: InvestigationState) -> dict[str, Any]:
        raw = state["raw_alert"]
        if not isinstance(raw, (str, dict)):
            raise ValueError("invalid alert input")
        started_at = clock().isoformat()
        fresh: dict[str, Any] = {
            "alert": {},
            "incident_window": {},
            "integrations": [],
            "recalled": [],
            "runbooks": [],
            "available_domains": [],
            "plan": {},
            "tasks": [],
            "findings": Overwrite([]),
            "evidence": Overwrite([]),
            "diagnosis": {},
            "verification": {},
            "actions": [],
            "report": {},
            "deliveries": [],
            "errors": Overwrite([]),
            "delivery_destinations": state.get("destinations") or {},
            "wave": 0,
            "budget": limits.max_total_tasks,
            "started_at": started_at,
        }
        if replay_intake is not None:
            recorded_alert, recorded_window = replay_intake
            _emit(state, "stage", "intake")
            return {
                **fresh,
                "alert": recorded_alert.model_dump(mode="json"),
                "incident_window": recorded_window.model_dump(mode="json"),
                "is_noise": False,
            }
        requested = state.get("user_requested", False)
        if isinstance(raw, str) and not requested and is_trivial_message(raw):
            _emit(state, "stage", "noise")
            return {**fresh, "is_noise": True}
        error: str | None = None
        try:
            if isinstance(raw, str) and not _is_json_object(raw):
                timeout = _remaining(started_at, clock, limits.deadline_seconds)
                extraction = await asyncio.wait_for(services.extract(raw), timeout=timeout)
                if extraction.is_noise and not requested:
                    _emit(state, "stage", "noise")
                    return {**fresh, "is_noise": True}
                alert = alert_from_extraction(extraction, raw)
            else:
                alert = parse_alert(raw, source_hint=state.get("source_hint"))
        except Exception as exc:
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError("invalid alert input") from None
            alert = parse_alert(raw)
            error = f"intake extraction failed: {type(exc).__name__}"
        window = resolve_window(alert.started_at, now=clock())
        _emit(state, "stage", "intake")
        result: dict[str, Any] = fresh | {
            "alert": alert.model_dump(mode="json"),
            "incident_window": window.model_dump(mode="json"),
            "is_noise": False,
        }
        if error:
            result["errors"] = [error]
        return result

    async def gather_context(
        state: InvestigationState, runtime: Runtime[RunContext]
    ) -> dict[str, Any]:
        tools = _safe_tools(registry.tools(_run_context(runtime, registry)))
        domains = available_specialists(tools)
        integrations = sorted(
            {meta.integration for tool in tools if (meta := read_tool_meta(tool)) is not None}
        )
        alert = NormalizedAlert.model_validate(state["alert"])
        category = alert.labels.get("category") or alert.annotations.get("category")
        runbooks = select_runbooks(alert.service, category)[:2]
        recalled: list[dict[str, Any]] = []
        errors: list[str] = []
        if memory is not None:
            try:
                query = " ".join(item for item in (alert.service, alert.title, category) if item)[
                    :500
                ]
                matches = await memory.search_incidents(query, limit=4)
                recalled = [
                    {
                        "id": str(item.get("id", ""))[:128],
                        "fingerprint": str(item.get("fingerprint", ""))[:64],
                        "title": str(item.get("title", ""))[:240],
                        "service": str(item.get("service", ""))[:120],
                        "category": str(item.get("category", ""))[:120],
                        "summary": str(item.get("summary", ""))[:500],
                    }
                    for item in matches
                    if item.get("id") != state.get("run_id")
                ][:3]
            except Exception as exc:
                errors.append(f"incident recall failed: {type(exc).__name__}")
        _emit(state, "stage", "gather_context")
        return {
            "integrations": integrations,
            "available_domains": [spec.domain.value for spec in domains],
            "recalled": recalled,
            "runbooks": runbooks,
            "errors": errors,
        }

    async def plan(state: InvestigationState) -> dict[str, Any]:
        alert = NormalizedAlert.model_validate(state["alert"])
        window = IncidentWindow.model_validate(state["incident_window"])
        available = [Domain(value) for value in state.get("available_domains", [])]
        findings = [SpecialistFinding.model_validate(item) for item in state.get("findings", [])]
        budget = state.get("budget", limits.max_total_tasks)
        max_tasks = min(limits.max_tasks_per_wave, budget)
        error: str | None = None
        if not available:
            chosen = fallback_plan(alert, available, max_tasks)
        else:
            try:
                timeout = _remaining(state["started_at"], clock, limits.deadline_seconds)
                candidate = await asyncio.wait_for(
                    services.plan(alert, window, available, findings), timeout=timeout
                )
                chosen = constrain_plan(candidate, available, max_tasks)
                if not chosen.tasks:
                    chosen = fallback_plan(alert, available, max_tasks)
            except Exception as exc:
                chosen = fallback_plan(alert, available, max_tasks)
                error = f"planner failed: {type(exc).__name__}"
        _emit(state, "stage", "plan")
        _emit(
            state,
            ""
            "",
            "plan",
            {
                "wave": state.get("wave", 0) + 1,
                "tasks": [
                    {"domain": task.domain.value, "hypothesis_ids": task.hypothesis_ids}
                    for task in chosen.tasks
                ],
            },
        )
        result: dict[str, Any] = {
            "plan": chosen.model_dump(mode="json"),
            "tasks": [task.model_dump(mode="json") for task in chosen.tasks],
            "wave": state.get("wave", 0) + 1,
            "budget": budget - len(chosen.tasks),
        }
        if error:
            result["errors"] = [error]
        return result

    def dispatch(state: InvestigationState) -> list[Send] | str:
        tasks = state.get("tasks", [])
        if not tasks:
            return "synthesize"
        return [
            Send(
                "specialist",
                {
                    "task": task,
                    "alert": state["alert"],
                    "incident_window": state["incident_window"],
                    "wave": state["wave"],
                    "started_at": state["started_at"],
                    "thread_id": state.get("thread_id", ""),
                    "run_id": state.get("run_id", ""),
                },
            )
            for task in tasks
        ]

    async def specialist(
        payload: InvestigationState, runtime: Runtime[RunContext]
    ) -> dict[str, Any]:
        task = SpecialistTask.model_validate(payload["task"])
        alert = NormalizedAlert.model_validate(payload["alert"])
        window = IncidentWindow.model_validate(payload["incident_window"])
        context = _run_context(runtime, registry)
        tools = read_tools(registry.tools(context), task.domain)
        try:
            timeout = _remaining(payload["started_at"], clock, limits.deadline_seconds)
            finding, evidence = await asyncio.wait_for(
                services.investigate(task, alert, window, tools, context),
                timeout=timeout,
            )
            error = None
        except Exception as exc:
            finding = degraded_finding(task.domain, f"{type(exc).__name__}: unavailable")
            evidence = []
            error = f"{task.domain.value} specialist failed: {type(exc).__name__}"
        _emit(
            payload,
            "specialist",
            task.domain.value,
            {
                "wave": payload["wave"],
                "domain": task.domain.value,
                "evidence_ids": [item.id for item in evidence],
            },
        )
        result: dict[str, Any] = {
            "findings": [{**finding.model_dump(mode="json"), "wave": payload["wave"]}],
            "evidence": [item.model_dump(mode="json") for item in evidence],
        }
        if error:
            result["errors"] = [error]
        return result

    async def synthesize(state: InvestigationState) -> dict[str, Any]:
        alert = NormalizedAlert.model_validate(state["alert"])
        chosen = InvestigationPlan.model_validate(state["plan"])
        findings = [SpecialistFinding.model_validate(item) for item in state.get("findings", [])]
        evidence = [EvidenceItem.model_validate(item) for item in state.get("evidence", [])]
        error: str | None = None
        if not evidence:
            diagnosis = unknown_diagnosis(alert, "no evidence was collected")
        else:
            try:
                timeout = _remaining(state["started_at"], clock, limits.deadline_seconds)
                contextual = getattr(services, "synthesize_with_context", None)
                synthesis = (
                    contextual(
                        alert,
                        chosen,
                        findings,
                        evidence,
                        state.get("recalled", []),
                        state.get("runbooks", []),
                    )
                    if contextual is not None
                    else services.synthesize(alert, chosen, findings, evidence)
                )
                diagnosis = await asyncio.wait_for(synthesis, timeout=timeout)
            except Exception as exc:
                diagnosis = unknown_diagnosis(alert, "synthesis unavailable")
                error = f"synthesizer failed: {type(exc).__name__}"
        _emit(state, "stage", "synthesize")
        result: dict[str, Any] = {"diagnosis": diagnosis.model_dump(mode="json")}
        if error:
            result["errors"] = [error]
        return result

    async def verify(state: InvestigationState) -> dict[str, Any]:
        alert = NormalizedAlert.model_validate(state["alert"])
        diagnosis = Diagnosis.model_validate(state["diagnosis"])
        evidence = [EvidenceItem.model_validate(item) for item in state.get("evidence", [])]
        report = verify_diagnosis(diagnosis, evidence, alert)
        by_id = {item.id: item for item in evidence}
        checks = list(report.claims)
        for index, claim in enumerate(diagnosis.claims):
            if checks[index].status is ClaimStatus.UNSUPPORTED:
                continue
            cited = [by_id[item] for item in claim.evidence_ids]
            try:
                timeout = _remaining(state["started_at"], clock, limits.deadline_seconds)
                judgment = await asyncio.wait_for(services.judge(claim, cited), timeout=timeout)
                reason = redact_secrets(judgment.reason)[:240]
                if judgment.supported:
                    checks[index] = checks[index].model_copy(
                        update={"status": ClaimStatus.SUPPORTED, "reason": reason}
                    )
                    continue
            except Exception as exc:
                reason = f"judge failed: {type(exc).__name__}"
            checks[index] = checks[index].model_copy(
                update={"status": ClaimStatus.UNSUPPORTED, "reason": reason}
            )
        report = report.with_claims(checks, diagnosis)
        _emit(state, "stage", "verify")
        return {"verification": report.model_dump(mode="json")}

    def after_verify(state: InvestigationState) -> str:
        verified = VerificationReport.model_validate(state["verification"])
        if (
            not verified.passed
            and state["wave"] < limits.max_waves
            and state["budget"] > 0
            and state.get("tasks")
            and _within_deadline(state, clock, limits.deadline_seconds)
        ):
            return "refine"
        return "propose_remediation"

    def refine(state: InvestigationState) -> dict[str, Any]:
        window = IncidentWindow.model_validate(state["incident_window"])
        if not any(
            _has_observations(EvidenceItem.model_validate(item))
            for item in state.get("evidence", [])
        ):
            window = window.expanded()
        _emit(state, "stage", "refine")
        return {"incident_window": window.model_dump(mode="json")}

    def propose_remediation(state: InvestigationState) -> dict[str, Any]:
        diagnosis = Diagnosis.model_validate(state["diagnosis"])
        verified = VerificationReport.model_validate(state["verification"])
        if not verified.passed:
            diagnosis = diagnosis.model_copy(
                update={
                    "category": UNKNOWN,
                    "root_cause": "Root cause not verified",
                    "confidence": min(diagnosis.confidence, 0.49),
                    "causal_chain": [],
                    "remediation_steps": [],
                }
            )
        steps = diagnosis.remediation_steps or template_steps(
            diagnosis.category, state.get("integrations", [])
        )
        actions = [
            RemediationAction(
                title=step, rationale="Proposed from the diagnosis; manual review required"
            )
            for step in steps[:5]
        ]
        _emit(state, "stage", "propose_remediation")
        return {
            "diagnosis": diagnosis.model_dump(mode="json"),
            "actions": [item.model_dump(mode="json") for item in actions],
        }

    async def report(state: InvestigationState) -> dict[str, Any]:
        result = build_report(
            NormalizedAlert.model_validate(state["alert"]),
            Diagnosis.model_validate(state["diagnosis"]),
            [EvidenceItem.model_validate(item) for item in state.get("evidence", [])],
            verification=VerificationReport.model_validate(state["verification"]),
            actions=[RemediationAction.model_validate(item) for item in state.get("actions", [])],
        )
        _emit(state, "report", "ready")
        response: dict[str, Any] = {"report": result.model_dump(mode="json")}
        if memory is not None and result.verification is not None and result.verification.passed:
            try:
                await memory.save_incident(
                    state.get("run_id") or str(uuid4()),
                    fingerprint=result.alert.fingerprint,
                    title=result.title,
                    service=result.alert.service or "",
                    category=result.diagnosis.category,
                    summary=result.diagnosis.root_cause,
                    evidence_ids=[item.id for item in result.evidence],
                )
            except Exception as exc:
                response["errors"] = [f"incident memory failed: {type(exc).__name__}"]
        return response

    async def deliver(state: InvestigationState, config: RunnableConfig) -> dict[str, Any]:
        destinations = state.get("delivery_destinations") or {}
        if not destinations:
            _emit(state, "done", "no delivery requested")
            return {"deliveries": []}
        if delivery is None:
            return {"deliveries": [], "errors": ["delivery not configured"]}
        report = Report.model_validate(state["report"])
        error: str | None = None
        try:
            execution_id = (
                state.get("run_id")
                or state.get("thread_id")
                or config.get("configurable", {}).get("thread_id")
                or str(uuid4())
            )
            sent = await delivery.send_report(report, destinations, execution_id)
            results = [{**asdict(item), "status": str(item.status)} for item in sent]
        except Exception as exc:
            results = []
            error = f"delivery failed: {type(exc).__name__}"
        _emit(state, "done", "delivery complete")
        result: dict[str, Any] = {"deliveries": results}
        if error:
            result["errors"] = [error]
        return result

    graph = StateGraph(InvestigationState, context_schema=RunContext)
    graph.add_node("intake", intake)
    graph.add_node("gather_context", gather_context)
    graph.add_node("plan", plan)
    graph.add_node("specialist", cast(Any, specialist))
    graph.add_node("synthesize", synthesize)
    graph.add_node("verify", verify)
    graph.add_node("refine", refine)
    graph.add_node("propose_remediation", propose_remediation)
    graph.add_node("report", report)
    graph.add_node("deliver", deliver)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges(
        "intake", lambda state: END if state.get("is_noise") else "gather_context"
    )
    graph.add_edge("gather_context", "plan")
    graph.add_conditional_edges("plan", dispatch, ["specialist", "synthesize"])
    graph.add_edge("specialist", "synthesize")
    graph.add_edge("synthesize", "verify")
    graph.add_conditional_edges("verify", after_verify, ["refine", "propose_remediation"])
    graph.add_edge("refine", "plan")
    graph.add_edge("propose_remediation", "report")
    graph.add_edge("report", "deliver")
    graph.add_edge("deliver", END)
    compiled = graph.compile(checkpointer=checkpointer)
    compiled.channels[START] = SanitizedInputChannel(InvestigationState)
    return compiled


def _safe_tools(tools: Sequence[BaseTool]) -> list[BaseTool]:
    return [
        tool
        for tool in tools
        if (meta := read_tool_meta(tool)) is not None
        and meta.side_effect_level is SideEffectLevel.READ_ONLY
    ]


def _has_observations(item: EvidenceItem) -> bool:
    if item.error:
        return False
    # Starting an asynchronous log query returns a handle, not an observation.
    if item.tool == "aws_logs_start":
        return False
    # Older evidence may contain only a summary, while tool artifacts carry data.
    return _has_result_data(item.summary if item.data is None else item.data)


def _has_result_data(value: Any) -> bool:
    """Ignore transport metadata when a result envelope contains no observations."""
    if value is None:
        return False
    if isinstance(value, str):
        if not value.strip():
            return False
        try:
            decoded = json.loads(value)
        except ValueError:
            return True
        return _has_result_data(decoded) if not isinstance(decoded, str) else bool(decoded.strip())
    if isinstance(value, (list, tuple)):
        return any(_has_result_data(item) for item in value)
    if isinstance(value, Mapping):
        result_keys = {
            "data",
            "result",
            "results",
            "items",
            "MetricDataResults",
            "MetricList",
            "MetricAlarms",
            "CompositeAlarms",
            "Values",
            "values",
            "DataPoints",
            "datapoints",
            "Events",
            "events",
            "logs",
            "text",
            "traces",
            "resourceSpans",
            "streams",
            "series",
            "samples",
        }
        results = [item for key, item in value.items() if key in result_keys]
        return any(_has_result_data(item) for item in results) if results else bool(value)
    # Zero-valued samples and False health flags are real observations.
    return True


def _run_context(runtime: Runtime[RunContext], registry: ToolSource) -> RunContext:
    """Use the caller's clients or build fresh ones for Studio's context-free runs."""
    if isinstance(runtime.context, RunContext):
        return runtime.context
    create = getattr(registry, "run_context", None)
    return create() if callable(create) else RunContext()


def _is_json_object(value: str) -> bool:
    if not value.lstrip().startswith("{"):
        return False
    try:
        return isinstance(json.loads(value), dict)
    except ValueError:
        return False


def _within_deadline(
    state: InvestigationState, clock: Callable[[], datetime], deadline_seconds: int
) -> bool:
    started = datetime.fromisoformat(state["started_at"])
    return (clock() - started).total_seconds() < deadline_seconds


def _remaining(started_at: str, clock: Callable[[], datetime], deadline_seconds: int) -> float:
    remaining = deadline_seconds - (clock() - datetime.fromisoformat(started_at)).total_seconds()
    if remaining <= 0:
        raise TimeoutError("investigation deadline elapsed")
    return remaining


def _emit(
    state: Mapping[str, Any], kind: str, label: str, data: Mapping[str, Any] | None = None
) -> None:
    """Emit progress when LangGraph is streaming; synchronous invokes may ignore it."""
    try:
        writer = get_stream_writer()
        writer(
            {
                "type": kind,
                "thread_id": state.get("thread_id", ""),
                "run_id": state.get("run_id", ""),
                "ts": datetime.now(UTC).isoformat(),
                "data": {"stage": label, **(data or {})},
            }
        )
    except RuntimeError:
        pass
