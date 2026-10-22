"""Versioned, sanitized incident recordings with exact offline tool replay."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from langchain_core.tools import StructuredTool

from sre_graph.domain.alerts.model import NormalizedAlert
from sre_graph.domain.tools import Domain, EvidenceType
from sre_graph.domain.window import IncidentWindow
from sre_graph.evals.scenarios import Answer
from sre_graph.integrations.payload import tool_payload
from sre_graph.safety.secrets import redact_value

_MAX_BYTES = 8 * 1024 * 1024


def canonical(tool: str, args: dict[str, Any]) -> str:
    return json.dumps(
        {"tool": tool, "args": args}, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _validate(recording: dict[str, Any]) -> None:
    if (
        not isinstance(recording, dict)
        or type(recording.get("version")) is not int
        or recording.get("version") != 1
        or not isinstance(recording.get("reviewer"), str)
        or not recording["reviewer"].strip()
    ):
        raise ValueError("unsupported recording or missing independent reviewer")
    Answer.model_validate(recording.get("answer"))
    if not isinstance(recording.get("alert"), dict) or not isinstance(
        recording.get("window"), dict
    ):
        raise ValueError("recording requires alert and incident window")
    NormalizedAlert.model_validate(recording["alert"])
    window = recording["window"]
    IncidentWindow.model_validate(
        {
            "since": window.get("since", window.get("start")),
            "until": window.get("until", window.get("end")),
            "source": window.get("source", "override"),
        }
    )
    if not isinstance(recording.get("provenance"), dict):
        raise ValueError("missing provenance")
    calls = recording.get("calls")
    if not isinstance(calls, list) or not calls or len(calls) > 1000:
        raise ValueError("recording requires bounded actual tool calls")
    keys = set()
    for call in calls:
        if (
            not isinstance(call, dict)
            or not isinstance(call.get("tool"), str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", call["tool"])
        ):
            raise ValueError("invalid tool name")
        if (
            not isinstance(call.get("args"), dict)
            or "result" not in call
            or not call.get("collected_at")
        ):
            raise ValueError("recording lacks actual arguments, result or capture time")
        moment = datetime.fromisoformat(call["collected_at"])
        if (
            moment.tzinfo is None
            or not isinstance(call.get("domains"), list)
            or not call["domains"]
        ):
            raise ValueError("recording requires timezone and specialist domains")
        for domain in call["domains"]:
            Domain(domain)
        EvidenceType(str(call.get("evidence_type")))
        key = canonical(call["tool"], call["args"])
        if key in keys:
            raise ValueError("ambiguous duplicate recorded call")
        keys.add(key)
    if len(json.dumps(recording, allow_nan=False).encode()) > _MAX_BYTES:
        raise ValueError("recording exceeds size limit")


def record_scenario(
    snapshot: dict[str, Any], answer: dict[str, Any], directory: Path, *, reviewer: str
) -> None:
    if directory.exists() or directory.is_symlink() or ".." in directory.parts:
        raise ValueError("recording requires a new directory without traversal")
    evidence = snapshot.get("evidence", [])
    calls = []
    for item in evidence:
        if not isinstance(item, dict) or "args" not in item or "data" not in item:
            raise ValueError("snapshot does not preserve actual tool arguments/results")
        calls.append(
            {
                "tool": item.get("tool"),
                "args": item["args"],
                "result": item["data"],
                "error": item.get("error"),
                "collected_at": item.get("collected_at"),
                "evidence_id": item.get("id"),
                "integration": item.get("integration"),
                "domains": item.get("domains", []),
                "evidence_type": item.get("evidence_type"),
                "summary": item.get("summary", ""),
            }
        )
    recording = redact_value(
        {
            "version": 1,
            "recorded_at": datetime.now(UTC).isoformat(),
            "reviewer": reviewer,
            "answer": Answer.model_validate(answer).model_dump(mode="json"),
            "alert": snapshot.get("alert"),
            "window": snapshot.get("incident_window", snapshot.get("window")),
            "provenance": {
                "thread_id": snapshot.get("thread_id", snapshot.get("run", {}).get("thread_id")),
                "run_id": snapshot.get("run_id", snapshot.get("run", {}).get("run_id")),
            },
            "calls": calls,
        }
    )
    _validate(recording)
    payload = json.dumps(recording, indent=2, ensure_ascii=False, allow_nan=False)
    directory.mkdir(parents=False, exist_ok=False)
    try:
        with (directory / "scenario.json").open("x", encoding="utf-8") as handle:
            handle.write(payload)
    except BaseException:
        directory.rmdir()
        raise


def load_recording(directory: Path) -> dict[str, Any]:
    path = directory / "scenario.json"
    if path.is_symlink() or path.stat().st_size > _MAX_BYTES:
        raise ValueError("invalid recording file")
    recording = json.loads(path.read_text(encoding="utf-8"))
    _validate(recording)
    return cast(dict[str, Any], recording)


class StrictReplay:
    """Only exact recorded calls execute; there is no live client or fallback."""

    def __init__(self, recording: dict[str, Any]) -> None:
        _validate(recording)
        self.recording = recording
        self.calls = {canonical(item["tool"], item["args"]): item for item in recording["calls"]}
        self.executed: list[str] = []
        self.refused = 0

    def call(self, tool: str, args: dict[str, Any]) -> Any:
        key = canonical(tool, args)
        if key not in self.calls:
            self.refused += 1
            raise ValueError("unrecorded tool call refused")
        item = self.calls[key]
        self.executed.append(key)
        if item.get("error"):
            raise ValueError("recorded tool call failed")
        return json.loads(json.dumps(item["result"]))

    def tools(self, *, artifacts: bool = False) -> list[StructuredTool]:
        tools = []

        def bind(name: str) -> Callable[..., Any]:
            def invoke(**kwargs: Any) -> Any:
                result = self.call(name, kwargs)
                return (tool_payload(result), result) if artifacts else result

            return invoke

        for name in sorted({item["tool"] for item in self.calls.values()}):
            tools.append(
                StructuredTool.from_function(
                    func=bind(name),
                    name=name,
                    description="Strict offline replay of a recorded read call.",
                    args_schema={
                        "type": "object",
                        "properties": {
                            key: {
                                "type": "string"
                                if isinstance(value, str)
                                else "boolean"
                                if isinstance(value, bool)
                                else "number"
                                if isinstance(value, (int, float))
                                else "object"
                                if isinstance(value, dict)
                                else "array"
                                if isinstance(value, list)
                                else "null"
                            }
                            for call in self.calls.values()
                            if call["tool"] == name
                            for key, value in call["args"].items()
                        },
                        "additionalProperties": True,
                    },
                    response_format="content_and_artifact" if artifacts else "content",
                    metadata={
                        "integration": "recording",
                        "domains": sorted(
                            {
                                domain
                                for call in self.calls.values()
                                if call["tool"] == name
                                for domain in call["domains"]
                            }
                        ),
                        "evidence_type": next(
                            call["evidence_type"]
                            for call in self.calls.values()
                            if call["tool"] == name
                        ),
                        "side_effect_level": "read_only",
                        "idempotent": True,
                    },
                )
            )
        return tools

    async def execute(self) -> dict[str, Any]:
        tools = {tool.name: tool for tool in self.tools()}
        for item in self.recording["calls"]:
            await tools[item["tool"]].ainvoke(item["args"])
        return {
            "version": 1,
            "model": "scripted",
            "baseline_eligible": False,
            "scenario": self.recording["provenance"].get("thread_id"),
            "status": "completed",
            "calls_executed": len(self.executed),
            "quality_measured": False,
        }


async def replay_graph(
    recording: dict[str, Any], *, model: str = "scripted", services: Any = None
) -> dict[str, Any]:
    """Run the production graph using only exact recorded integration responses.

    A real model may call its LLM provider; integration calls never use network.
    Scripted replay diagnoses unknown and can never become a quality baseline.
    """
    import time
    from collections.abc import Collection, Sequence
    from uuid import uuid4

    from langchain_core.tools import BaseTool

    from sre_graph.config.tracing import disable_external_tracing
    from sre_graph.domain.alerts.model import NormalizedAlert
    from sre_graph.domain.diagnosis import Claim, Diagnosis, unknown_diagnosis
    from sre_graph.domain.evidence import EvidenceItem, evidence_id
    from sre_graph.domain.investigation import (
        Hypothesis,
        InvestigationPlan,
        SpecialistFinding,
        SpecialistTask,
    )
    from sre_graph.domain.tools import read_tool_meta
    from sre_graph.evals.runner import _real_services
    from sre_graph.evals.scenarios import Scenario
    from sre_graph.evals.scoring import score_scenario
    from sre_graph.graphs.investigation.services import ClaimJudgment, UnavailableServices
    from sre_graph.harness.runtime import make_investigation_graph
    from sre_graph.integrations.context import RunContext

    disable_external_tracing()
    replay = StrictReplay(recording)
    replay_tools = replay.tools(artifacts=True)

    class ReplayRegistry:
        def tools(
            self, context: RunContext, domains: Collection[Domain] | None = None
        ) -> list[BaseTool]:
            return [
                tool
                for tool in replay_tools
                if domains is None
                or ((meta := read_tool_meta(tool)) is not None and meta.domains & set(domains))
            ]

    class RecordedServices(UnavailableServices):
        async def plan(
            self,
            alert: NormalizedAlert,
            window: IncidentWindow,
            available: Sequence[Domain],
            findings: Sequence[SpecialistFinding],
        ) -> InvestigationPlan:
            return InvestigationPlan(
                hypotheses=[
                    Hypothesis(
                        id="R1",
                        statement="Recorded wiring check",
                        confirm_if="Recorded call succeeds",
                        rule_out_if="Recorded call fails",
                    )
                ],
                tasks=[
                    SpecialistTask(domain=domain, objective="Replay exact recorded reads")
                    for domain in available
                ],
            )

        async def investigate(
            self,
            task: SpecialistTask,
            alert: NormalizedAlert,
            window: IncidentWindow,
            tools: Sequence[BaseTool],
            context: RunContext,
        ) -> tuple[SpecialistFinding, list[EvidenceItem]]:
            selected = {tool.name: tool for tool in tools}
            evidence = []
            for item in recording["calls"]:
                if item["tool"] not in selected or task.domain.value not in item["domains"]:
                    continue
                message = await selected[item["tool"]].ainvoke(
                    {
                        "name": item["tool"],
                        "args": item["args"],
                        "id": str(uuid4()),
                        "type": "tool_call",
                    }
                )
                evidence.append(
                    EvidenceItem(
                        id=evidence_id(item["tool"], item["args"]),
                        integration="recording",
                        tool=item["tool"],
                        args=item["args"],
                        data=message.artifact,
                        domains=[task.domain],
                        evidence_type=item["evidence_type"],
                        summary=item["summary"],
                        collected_at=datetime.fromisoformat(item["collected_at"]),
                        error=item.get("error"),
                    )
                )
            return SpecialistFinding(
                domain=task.domain,
                summary="Recorded reads replayed",
                evidence_ids=[item.id for item in evidence],
                confidence=0,
            ), evidence

        async def synthesize(
            self,
            alert: NormalizedAlert,
            plan: InvestigationPlan,
            findings: Sequence[SpecialistFinding],
            evidence: Sequence[EvidenceItem],
        ) -> Diagnosis:
            return unknown_diagnosis(alert, "scripted recording replay does not infer root cause")

        async def judge(self, claim: Claim, evidence: Sequence[EvidenceItem]) -> ClaimJudgment:
            return ClaimJudgment(supported=False, reason="scripted replay does not judge claims")

    started = time.perf_counter()
    window = recording["window"]
    recorded_window = IncidentWindow.model_validate(
        {
            "since": window.get("since", window.get("start")),
            "until": window.get("until", window.get("end")),
            "source": window.get("source", "override"),
        }
    )
    reference = recorded_window.until
    graph = make_investigation_graph(
        services or (RecordedServices() if model == "scripted" else _real_services(model)),
        ReplayRegistry(),
        replay_intake=(NormalizedAlert.model_validate(recording["alert"]), recorded_window),
        clock=lambda: reference + timedelta(seconds=time.perf_counter() - started),
    )
    run_id = str(uuid4())
    state = await graph.ainvoke(
        {
            "raw_alert": recording["alert"],
            "user_requested": True,
            "destinations": {},
            "thread_id": run_id,
            "run_id": run_id,
        },
        context=RunContext(clients={"recording": replay}),
        config={"recursion_limit": 64},
    )
    scenario_id = str(recording["provenance"].get("thread_id") or "recorded-incident")
    scenario = Scenario(
        suite="recorded",
        scenario_id=scenario_id,
        alert=recording["alert"],
        metadata={},
        answer=Answer.model_validate(recording["answer"]),
        evidence={},
    )
    score = score_scenario(scenario, state, elapsed=time.perf_counter() - started).model_dump(
        mode="json"
    )
    good_evidence = [
        item
        for item in state.get("evidence", [])
        if not item.get("error") and item.get("data") is not None
    ]
    used_tools = {item["tool"] for item in good_evidence}
    used_sources = used_tools | {
        item["integration"] for item in recording["calls"] if item["tool"] in used_tools
    }
    reference_answer = scenario.answer
    score["used_sources"] = sorted(used_sources)
    score["required_sources_found"] = sum(
        source in used_sources for source in reference_answer.required_evidence_sources
    )
    score["trajectory_covered"] = sum(
        tool in used_tools for tool in reference_answer.optimal_trajectory
    )
    score["trajectory_unavailable"] = [
        tool for tool in reference_answer.optimal_trajectory if tool not in used_tools
    ]
    score["required_queries_found"] = sum(
        tool in used_tools for tool in reference_answer.required_queries
    )
    errors = list(state.get("errors", []))
    if (
        replay.refused
        or not replay.executed
        or any(item.get("error") for item in state.get("evidence", []))
    ):
        errors.append({"type": "strict_replay_failed"})
    status = "failed" if errors else "completed"
    return {
        "dataset_fingerprint": hashlib.sha256(
            json.dumps(recording, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "suite": "recorded",
        "model": model,
        "judge_model": None,
        "repeat": 1,
        "baseline_eligible": model != "scripted" and not errors,
        "runs": [
            {
                "scenario": scenario.key,
                "attempt": 1,
                "status": status,
                "score": score,
                "graph_errors": errors,
                "calls_executed": len(replay.executed),
            }
        ],
    }
