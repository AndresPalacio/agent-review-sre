"""A bounded, public explanation of an analysis: the graph it ran and the work it recorded.

This is an execution summary, not an LLM reasoning trace. Tool arguments, raw market data and
private agent prompts are never copied into the response: only stages, analyst signals, the
evidence ids, each ticker's thesis, its verification and the portfolio decision.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

MAX_EXPLAIN_EVENTS = 500


TOPOLOGY: dict[str, Any] = {
    "patterns": ["workflow", "plan_and_execute", "parallel_analysts", "reflection"],
    "nodes": [
        {"id": "start", "label": "Petición o señal", "kind": "input"},
        {"id": "intake", "label": "Normalizar petición", "kind": "intake"},
        {"id": "gather_context", "label": "Contexto y tesis previas", "kind": "context"},
        {"id": "plan", "label": "Planificar hipótesis", "kind": "planner"},
        {"id": "analyst", "label": "Analistas en paralelo", "kind": "analysts"},
        {"id": "synthesize", "label": "Sintetizar tesis", "kind": "synthesis"},
        {"id": "verify", "label": "Verificar afirmaciones", "kind": "critic"},
        {"id": "refine", "label": "Ampliar ventana y repetir", "kind": "loop"},
        {"id": "risk", "label": "Límites de riesgo", "kind": "risk"},
        {"id": "portfolio", "label": "Decisión de cartera", "kind": "decision"},
        {"id": "report", "label": "Construir informe", "kind": "report"},
        {"id": "deliver", "label": "Entregar informe", "kind": "delivery"},
        {"id": "end", "label": "Fin", "kind": "output"},
    ],
    "edges": [
        {"source": "start", "target": "intake"},
        {"source": "intake", "target": "gather_context", "label": "tickers"},
        {"source": "intake", "target": "end", "label": "noise"},
        {"source": "gather_context", "target": "plan"},
        {"source": "plan", "target": "analyst", "label": "tasks"},
        {"source": "plan", "target": "synthesize", "label": "no_tasks"},
        {"source": "analyst", "target": "synthesize"},
        {"source": "synthesize", "target": "verify"},
        {"source": "verify", "target": "refine", "label": "needs_more_evidence"},
        {"source": "verify", "target": "risk", "label": "verified_or_limit"},
        {"source": "refine", "target": "plan"},
        {"source": "risk", "target": "portfolio"},
        {"source": "portfolio", "target": "report"},
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


def _signals(value: Any) -> dict[str, str]:
    """``{ticker: signal}`` from an event's map or a finding's signal list."""
    if isinstance(value, Mapping):
        return {str(k)[:16]: str(v)[:16] for k, v in list(value.items())[:10]}
    return {
        str(item.get("ticker"))[:16]: str(item.get("signal"))[:16]
        for item in _objects(value)[:10]
        if item.get("ticker")
    }


def _step(event: Mapping[str, Any]) -> dict[str, Any]:
    data = _object(event.get("data"))
    kind = str(event.get("type", ""))[:64]
    stage = data.get("stage") if isinstance(data.get("stage"), str) else None
    status = data.get("status") if isinstance(data.get("status"), str) else None
    domain = data.get("domain") if isinstance(data.get("domain"), str) else None
    node = stage if stage in _NODE_IDS else kind if kind in _NODE_IDS else None
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
    if kind == "analyst":
        result["evidence_ids"] = _strings(data.get("evidence_ids"))
        result["signals"] = _signals(data.get("signals"))
    if kind == "plan":
        result["tasks"] = [
            {"domain": str(item.get("domain"))[:64], "provenance": "observed"}
            for item in _objects(data.get("tasks"))
            if isinstance(item.get("domain"), str)
        ]
    return result


def _thesis(value: Mapping[str, Any], verification: Mapping[str, Any]) -> dict[str, Any]:
    ticker = str(value.get("ticker", ""))[:16]
    check = _object(verification.get(ticker))
    return {
        "ticker": ticker,
        "stance": value.get("stance"),
        "category": value.get("category"),
        "confidence": value.get("confidence"),
        "summary": str(value.get("summary", ""))[:1000],
        "verified": check.get("passed"),
        "claims": [
            {
                "statement": str(item.get("statement", ""))[:500],
                "status": item.get("status"),
                "reason": str(item.get("reason", ""))[:500],
            }
            for item in _objects(check.get("claims"))[:12]
        ],
        "provenance": "derived",
    }


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
            number, {"number": number, "provenance": "derived", "tasks": [], "analysts": []}
        )

    for step in steps:
        number = _wave(step.get("wave"))
        if number is None:
            continue
        wave = get_wave(number)
        if step["type"] == "plan":
            wave["provenance"] = "observed"
            wave["tasks"] = step["tasks"]
        elif step["type"] == "analyst" and step.get("domain"):
            wave["provenance"] = "observed"
            wave["analysts"].append(
                {
                    "domain": step["domain"],
                    "evidence_ids": step["evidence_ids"],
                    "signals": step["signals"],
                    "provenance": "observed",
                }
            )

    findings: list[dict[str, Any]] = []
    for item in _objects(values.get("findings")):
        domain = item.get("domain")
        number = _wave(item.get("wave"))
        if not isinstance(domain, str) or not domain:
            continue
        finding = {
            "domain": domain[:64],
            "wave": number,
            "summary": str(item.get("summary", ""))[:1000],
            "confidence": item.get("confidence"),
            "signals": _signals(item.get("signals")),
            "evidence_ids": _strings(item.get("evidence_ids")),
            "degraded": bool(item.get("degraded")),
            "provenance": "derived",
        }
        findings.append(finding)
        if number is not None:
            wave = get_wave(number)
            if not any(analyst["domain"] == domain for analyst in wave["analysts"]):
                wave["analysts"].append(
                    {
                        "domain": domain[:64],
                        "evidence_ids": finding["evidence_ids"],
                        "signals": finding["signals"],
                        "provenance": "derived",
                    }
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
    decisions = _object(values.get("decisions"))
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
            "theses": [_thesis(item, verification) for item in _objects(values.get("theses"))],
            "decisions": {
                str(ticker)[:16]: {
                    "action": _object(value).get("action"),
                    "quantity": _object(value).get("quantity"),
                    "reasoning": str(_object(value).get("reasoning", ""))[:500],
                    "provenance": "derived",
                }
                for ticker, value in list(decisions.items())[:10]
            },
            "orders": [
                {
                    "client_order_id": item.get("client_order_id"),
                    "ticker": item.get("ticker"),
                    "action": item.get("action"),
                    "quantity": item.get("quantity"),
                    "provenance": "derived",
                }
                for item in _objects(values.get("orders"))[:10]
            ],
            "degraded": _strings(values.get("degraded")),
        },
    }
