"""Seven stable specialist identities and read-only tool routing."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from langchain_core.tools import BaseTool

from sre_graph.domain.tools import Domain, SideEffectLevel, read_tool_meta


@dataclass(frozen=True)
class SpecialistSpec:
    domain: Domain
    description: str
    prompt: str
    max_tool_calls: int = 8
    max_model_calls: int = 10


SPECIALISTS: dict[Domain, SpecialistSpec] = {
    Domain.KUBERNETES: SpecialistSpec(
        Domain.KUBERNETES, "Pods, workloads, events and cluster health", "Inspect Kubernetes state."
    ),
    Domain.METRICS: SpecialistSpec(
        Domain.METRICS, "Time series and saturation", "Compare metrics inside the incident window."
    ),
    Domain.LOGS: SpecialistSpec(
        Domain.LOGS, "Application and platform logs", "Find errors in bounded logs."
    ),
    Domain.TRACES: SpecialistSpec(
        Domain.TRACES, "Distributed traces and spans", "Trace the affected request path."
    ),
    Domain.CLOUD: SpecialistSpec(
        Domain.CLOUD, "AWS infrastructure and cloud events", "Inspect cloud state and events."
    ),
    Domain.CHANGES: SpecialistSpec(
        Domain.CHANGES,
        "Deployments, revisions and configuration changes",
        "Correlate recent changes.",
    ),
    Domain.ERRORS: SpecialistSpec(
        Domain.ERRORS, "Exceptions and error issues", "Inspect errors and stack traces."
    ),
}


def read_tools(tools: Iterable[BaseTool], domain: Domain) -> list[BaseTool]:
    """Only tools explicitly classified as read-only for this domain."""
    selected: list[BaseTool] = []
    for candidate in tools:
        meta = read_tool_meta(candidate)
        if meta and domain in meta.domains and meta.side_effect_level is SideEffectLevel.READ_ONLY:
            selected.append(candidate)
    return selected


def available_specialists(tools: Iterable[BaseTool]) -> list[SpecialistSpec]:
    """Available domains in the registry's configured, safe tool set."""
    candidates = list(tools)
    return [spec for spec in SPECIALISTS.values() if read_tools(candidates, spec.domain)]
