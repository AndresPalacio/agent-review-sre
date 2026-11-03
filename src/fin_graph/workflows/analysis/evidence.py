"""Tool results as citable evidence, and the quant scores the compute tools left in them."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from pydantic import ValidationError

from fin_graph.domain.evidence import EvidenceItem, evidence_id
from fin_graph.domain.quant.common import QuantScore
from fin_graph.domain.tools import Domain, read_tool_meta
from fin_graph.safety.secrets import redact_text, redact_value


def evidence_from_message(
    tool: BaseTool, args: dict[str, Any], message: ToolMessage, domain: Domain
) -> EvidenceItem | None:
    """One tool call as evidence; the id depends only on the tool and its arguments."""
    meta = read_tool_meta(tool)
    if meta is None:
        return None
    clean_args = redact_value(args)
    content = redact_text(str(message.content))[:1000]
    data = redact_value(message.artifact if message.artifact is not None else message.content)
    summary = content if message.status == "error" else _one_line(data, content)
    return EvidenceItem(
        id=evidence_id(tool.name, clean_args),
        integration=meta.integration,
        tool=tool.name,
        evidence_type=meta.evidence_type,
        domains=[domain],
        summary=summary,
        args=clean_args,
        data=json.loads(json.dumps(data, default=str)),
        collected_at=datetime.now(UTC),
        error=content if message.status == "error" else None,
    )


def collect_evidence(
    messages: Sequence[Any], tools: Sequence[BaseTool], domain: Domain
) -> list[EvidenceItem]:
    """Evidence from an agent's transcript: every answered call to one of ``tools``."""
    by_name = {candidate.name: candidate for candidate in tools}
    calls: dict[str, tuple[str, dict[str, Any]]] = {}
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                if call["name"] in by_name and call["id"] is not None:
                    calls[call["id"]] = (call["name"], call["args"])
    evidence: list[EvidenceItem] = []
    for message in messages:
        if not isinstance(message, ToolMessage) or message.tool_call_id not in calls:
            continue
        name, args = calls[message.tool_call_id]
        item = evidence_from_message(by_name[name], args, message, domain)
        if item is not None:
            evidence.append(item)
    return evidence


def for_ticker(evidence: Iterable[EvidenceItem], ticker: str) -> list[EvidenceItem]:
    """Evidence whose tool call named ``ticker``."""
    return [item for item in evidence if str(item.args.get("ticker", "")).upper() == ticker]


def score_of(item: EvidenceItem) -> QuantScore | None:
    """The quant score a successful compute tool call carries, if any."""
    if item.error or not item.tool.startswith("compute_") or not isinstance(item.data, dict):
        return None
    try:
        return QuantScore.model_validate(item.data)
    except ValidationError:
        return None


def scores_by_domain(
    evidence: Iterable[EvidenceItem], ticker: str
) -> tuple[dict[Domain, QuantScore], dict[Domain, str]]:
    """The latest score per analyst for ``ticker`` and the evidence id that holds it."""
    scores: dict[Domain, QuantScore] = {}
    ids: dict[Domain, str] = {}
    for item in for_ticker(evidence, ticker):
        score = score_of(item)
        if score is not None:
            scores[score.analyst] = score
            ids[score.analyst] = item.id
    return scores, ids


def _one_line(data: Any, content: str) -> str:
    """A report line: a score's verdict, a list's size, or the start of the tool's text."""
    if isinstance(data, dict) and {"analyst", "ticker", "signal", "confidence"} <= data.keys():
        line = (
            f"{data['ticker']} {data['analyst']}: {data['signal']} {float(data['confidence']):.0f}%"
        )
        score = data.get("score")
        if isinstance(score, int | float) and data["analyst"] == "valuation":
            line += f", intrinsic value gap {score:+.1%}"
        elif isinstance(score, int | float) and data.get("max_score"):
            line += f", score {score:g}/{data['max_score']:g}"
        gaps = data.get("data_gaps") or []
        return line + (f" ({len(gaps)} data gaps)" if gaps else "")
    if isinstance(data, list):
        return f"{len(data)} rows: {content[:200]}"
    return content[:300]
