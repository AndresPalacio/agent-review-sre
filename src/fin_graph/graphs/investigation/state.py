"""JSON-only investigation state shared by the graph's parallel branches."""

from __future__ import annotations

import operator
from collections.abc import Iterable, Mapping, Sequence
from typing import Annotated, Any, TypedDict, cast

from langgraph.channels.ephemeral_value import EphemeralValue

from fin_graph.domain.evidence import merge_evidence
from fin_graph.safety.secrets import redact_text, redact_value


class SanitizedInputChannel(EphemeralValue[Any]):
    """Redact START before LangGraph can include the request in a checkpoint."""

    def update(self, values: Sequence[Any]) -> bool:
        cleaned = []
        for value in values:
            if isinstance(value, dict):
                item = redact_value(value)
                if isinstance(value.get("raw_alert"), (dict, str)):
                    item["raw_alert"] = redact_raw_alert(None, value["raw_alert"])
                cleaned.append(item)
            else:
                cleaned.append(redact_value(value))
        return super().update(cleaned)


def redact_raw_alert(_current: object, new: object) -> dict[str, Any] | str:
    """Scrub input at the state-channel boundary, before a checkpoint can store it."""
    if isinstance(new, str):
        return redact_text(new)
    if isinstance(new, dict):
        return cast(dict[str, Any], redact_value(new))
    raise ValueError("alert input must be an object or text")


def merge_findings(
    current: Iterable[Mapping[str, Any]] | None, new: Iterable[Mapping[str, Any]] | None
) -> list[dict[str, Any]]:
    """One latest finding per wave and specialist, preserving first-seen order."""
    merged: dict[tuple[int, str], dict[str, Any]] = {}
    for item in [*(current or []), *(new or [])]:
        merged[(int(item["wave"]), str(item["domain"]))] = dict(item)
    return list(merged.values())


class InvestigationState(TypedDict, total=False):
    raw_alert: Annotated[object, redact_raw_alert]
    source_hint: Annotated[str | None, EphemeralValue]
    user_requested: Annotated[bool, EphemeralValue]
    destinations: Annotated[dict[str, str], EphemeralValue]
    delivery_destinations: dict[str, str]
    alert: dict[str, Any]
    is_noise: bool
    incident_window: dict[str, Any]
    integrations: list[str]
    recalled: list[dict[str, Any]]
    runbooks: list[dict[str, str]]
    available_domains: list[str]
    plan: dict[str, Any]
    tasks: list[dict[str, Any]]
    task: dict[str, Any]
    findings: Annotated[list[dict[str, Any]], merge_findings]
    evidence: Annotated[list[dict[str, Any]], merge_evidence]
    diagnosis: dict[str, Any]
    verification: dict[str, Any]
    actions: list[dict[str, Any]]
    wave: int
    budget: int
    report: dict[str, Any]
    deliveries: list[dict[str, Any]]
    errors: Annotated[list[str], operator.add]
    started_at: str
    thread_id: str
    run_id: str
    harness: dict[str, Any]
    harness_trace: Annotated[list[dict[str, Any]], operator.add]
