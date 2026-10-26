"""A bounded, public explanation of an investigation's graph and recorded work.

This is an execution summary, not an LLM reasoning trace. Tool arguments, raw
responses and private agent prompts are never copied into the response.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

MAX_EXPLAIN_EVENTS = 500


TOPOLOGY: dict[str, Any] = {
    "patterns": ["workflow", "plan_and_execute", "parallel_specialists", "reflection"],
    "nodes": [
        {"id": "start", "label": "Pregunta o alerta", "kind": "input"},
        {"id": "intake", "label": "Normalizar entrada", "kind": "intake"},
        {"id": "gather_context", "label": "Reunir contexto", "kind": "context"},
        {"id": "plan", "label": "Planificar investigación", "kind": "planner"},
        {"id": "specialist", "label": "Especialistas en paralelo", "kind": "specialists"},
        {"id": "synthesize", "label": "Sintetizar hallazgos", "kind": "synthesis"},
        {"id": "verify", "label": "Verificar afirmaciones", "kind": "critic"},
        {"id": "refine", "label": "Ampliar y replantear", "kind": "loop"},
        {"id": "propose_remediation", "label": "Proponer acciones", "kind": "proposal"},
        {"id": "report", "label": "Construir informe", "kind": "report"},
        {"id": "deliver", "label": "Entregar informe", "kind": "delivery"},
        {"id": "end", "label": "Fin", "kind": "output"},
    ],
    "edges": [
        {"source": "start", "target": "intake"},
        {"source": "intake", "target": "gather_context", "label": "signal"},
        {"source": "intake", "target": "end", "label": "noise"},
        {"source": "gather_context", "target": "plan"},
        {"source": "plan", "target": "specialist", "label": "tasks"},
        {"source": "plan", "target": "synthesize", "label": "no_tasks"},
        {"source": "specialist", "target": "synthesize"},
        {"source": "synthesize", "target": "verify"},
        {"source": "verify", "target": "refine", "label": "needs_more_evidence"},
        {"source": "verify", "target": "propose_remediation", "label": "verified_or_limit"},
        {"source": "refine", "target": "plan"},
        {"source": "propose_remediation", "target": "report"},
        {"source": "report", "target": "deliver"},
        {"source": "deliver", "target": "end"},
    ],
}

_NODE_IDS = {node["id"] for node in TOPOLOGY["nodes"]}


def _object(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _objects(value: Any) -> list[Mapping[str, Any]]:
    return [_object(item) for item in value] if isinstance(value, list) else []


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item[:128] for item in value if isinstance(item, str)][:20]


def _wave(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 100:
        return value
    return None


def _task(value: Mapping[str, Any], provenance: str) -> dict[str, Any] | None:
    domain = value.get("domain")
    if not isinstance(domain, str) or not domain:
        return None
    return {
        "domain": domain[:64],
        "hypothesis_ids": _strings(value.get("hypothesis_ids")),
        "provenance": provenance,
    }


def _step(event: Mapping[str, Any]) -> dict[str, Any]:
    data = _object(event.get("data"))
    kind = str(event.get("type", ""))[:64]
    stage = data.get("stage") if isinstance(data.get("stage"), str) else None
    status = data.get("status") if isinstance(data.get("status"), str) else None
    domain = data.get("domain") if isinstance(data.get("domain"), str) else None
    node = stage if stage in _NODE_IDS else kind if kind in _NODE_IDS else None
    if kind == "specialist":
        node = "specialist"
    result: dict[str, Any] = {
        "id": event.get("id"),
        "run_id": event.get("run_id"),
        "type": kind,
        "node": node,
        "label": (stage or status or domain or kind)[:80],
        "ts": event.get("ts"),
        "provenance": "observed",
    }
    wave = _wave(data.get("wave"))
    if wave is not None:
        result["wave"] = wave
    if domain:
        result["domain"] = domain[:64]
    if status:
        result["status"] = status[:64]
    if kind == "specialist":
        result["evidence_ids"] = _strings(data.get("evidence_ids"))
    if kind == "plan":
        result["tasks"] = [
            task
            for item in _objects(data.get("tasks"))
            if (task := _task(item, "observed")) is not None
        ]
    return result


def build_explanation(
    thread_id: str,
    run: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    values: Mapping[str, Any],
    *,
    events_truncated: bool = False,
) -> dict[str, Any]:
    """Project persisted events and the latest checkpoint into a safe explanation."""
    steps = [_step(event) for event in events]
    waves: dict[int, dict[str, Any]] = {}

    def get_wave(number: int) -> dict[str, Any]:
        return waves.setdefault(
            number,
            {"number": number, "provenance": "derived", "tasks": [], "specialists": []},
        )

    for step in steps:
        number = _wave(step.get("wave"))
        if number is None:
            continue
        wave = get_wave(number)
        if step["type"] == "plan":
            wave["provenance"] = "observed"
            wave["tasks"] = step["tasks"]
        elif step["type"] == "specialist" and step.get("domain"):
            wave["provenance"] = "observed"
            wave["specialists"].append(
                {
                    "domain": step["domain"],
                    "evidence_ids": step["evidence_ids"],
                    "provenance": "observed",
                }
            )

    latest_wave = _wave(values.get("wave"))
    if latest_wave is not None:
        latest = get_wave(latest_wave)
        if not latest["tasks"]:
            latest["tasks"] = [
                task
                for item in _objects(_object(values.get("plan")).get("tasks"))
                if (task := _task(item, "derived")) is not None
            ]

    findings: list[dict[str, Any]] = []
    for item in _objects(values.get("findings")):
        domain = item.get("domain")
        number = _wave(item.get("wave"))
        if not isinstance(domain, str) or not domain:
            continue
        evidence_ids = _strings(item.get("evidence_ids"))
        finding = {
            "domain": domain[:64],
            "wave": number,
            "summary": str(item.get("summary", ""))[:1000],
            "confidence": item.get("confidence"),
            "evidence_ids": evidence_ids,
            "provenance": "derived",
        }
        findings.append(finding)
        if number is not None:
            wave = get_wave(number)
            if not any(specialist["domain"] == domain for specialist in wave["specialists"]):
                wave["specialists"].append(
                    {"domain": domain[:64], "evidence_ids": evidence_ids, "provenance": "derived"}
                )

    evidence = [
        {
            "id": item.get("id"),
            "integration": item.get("integration"),
            "tool": item.get("tool"),
            "provenance": "derived",
        }
        for item in _objects(values.get("evidence"))
        if isinstance(item.get("id"), str)
    ]
    verification = _object(values.get("verification"))
    diagnosis = _object(values.get("diagnosis"))
    return {
        "thread_id": thread_id,
        "run": dict(run),
        "topology": TOPOLOGY,
        "execution": {
            "event_scope": "thread",
            "steps_limit": MAX_EXPLAIN_EVENTS,
            "steps": steps,
            "steps_truncated": events_truncated,
            "next_after_id": events[-1].get("id") if events_truncated and events else None,
            "waves": [waves[number] for number in sorted(waves)],
            "findings": findings,
            "evidence": evidence,
            "verification": {
                "passed": verification.get("passed"),
                "score": verification.get("score"),
                "missing_sections": _strings(verification.get("missing_sections")),
                "claims": [
                    {
                        "statement": str(item.get("statement", ""))[:500],
                        "status": item.get("status"),
                        "reason": str(item.get("reason", ""))[:500],
                    }
                    for item in _objects(verification.get("claims"))
                ],
                "provenance": "derived",
            },
            "diagnosis": {
                "category": diagnosis.get("category"),
                "root_cause": str(diagnosis.get("root_cause", ""))[:1000],
                "confidence": diagnosis.get("confidence"),
                "provenance": "derived",
            },
        },
    }
