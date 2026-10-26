"""JSON-only analysis state shared by the graph's parallel analyst branches."""

from __future__ import annotations

import operator
from collections.abc import Iterable, Mapping, Sequence
from typing import Annotated, Any, TypedDict, cast

from langgraph.channels.ephemeral_value import EphemeralValue

from fin_graph.domain.evidence import merge_evidence
from fin_graph.safety.secrets import redact_text, redact_value


class SanitizedRequestChannel(EphemeralValue[Any]):
    """Redact the START input before LangGraph can store it in a checkpoint."""

    def update(self, values: Sequence[Any]) -> bool:
        return super().update([redact_value(value) for value in values])


def redact_raw_request(_current: object, new: object) -> dict[str, Any] | str:
    """Scrub input at the state-channel boundary, before a checkpoint can store it."""
    if isinstance(new, str):
        return redact_text(new)
    if isinstance(new, dict):
        return cast(dict[str, Any], redact_value(new))
    raise ValueError("analysis input must be an object or text")


def merge_findings(
    current: Iterable[Mapping[str, Any]] | None, new: Iterable[Mapping[str, Any]] | None
) -> list[dict[str, Any]]:
    """One latest finding per wave and analyst, preserving first-seen order."""
    merged: dict[tuple[int, str], dict[str, Any]] = {}
    for item in [*(current or []), *(new or [])]:
        merged[(int(item["wave"]), str(item["domain"]))] = dict(item)
    return list(merged.values())


def latest_findings(findings: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The newest finding per analyst across waves (a retried analyst replaces its first try)."""
    latest: dict[str, dict[str, Any]] = {}
    for item in sorted(findings, key=lambda value: int(value["wave"])):
        latest[str(item["domain"])] = dict(item)
    return list(latest.values())


class AnalysisState(TypedDict, total=False):
    raw_request: Annotated[object, redact_raw_request]
    source_hint: Annotated[str | None, EphemeralValue]
    user_requested: Annotated[bool, EphemeralValue]
    destinations: Annotated[dict[str, str], EphemeralValue]
    portfolio: dict[str, Any]
    delivery_destinations: dict[str, str]
    request: dict[str, Any]
    is_noise: bool
    window: dict[str, Any]
    integrations: list[str]
    available_domains: list[str]
    recalled: list[dict[str, Any]]
    plan: dict[str, Any]
    tasks: list[dict[str, Any]]
    task: dict[str, Any]
    findings: Annotated[list[dict[str, Any]], merge_findings]
    evidence: Annotated[list[dict[str, Any]], merge_evidence]
    theses: list[dict[str, Any]]
    verification: dict[str, dict[str, Any]]
    playbooks: dict[str, list[str]]
    limits: dict[str, dict[str, Any]]
    allowed: dict[str, dict[str, int]]
    decisions: dict[str, dict[str, Any]]
    orders: list[dict[str, Any]]
    wave: int
    budget: int
    report: dict[str, Any]
    deliveries: list[dict[str, Any]]
    degraded: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]
    started_at: str
    thread_id: str
    run_id: str
    harness: dict[str, Any]
    harness_trace: Annotated[list[dict[str, Any]], operator.add]
