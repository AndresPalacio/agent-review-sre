"""Read-only Prometheus and Mimir tools for metrics specialists."""

from __future__ import annotations

from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool

from fin_graph.domain.tools import Domain, EvidenceType, ToolMeta
from fin_graph.integrations.context import RunContext, require_client
from fin_graph.integrations.payload import tool_payload
from fin_graph.integrations.prometheus.client import PrometheusClient
from fin_graph.integrations.tooling import integration_tool


def _meta(integration: str, kind: EvidenceType = EvidenceType.METRICS) -> ToolMeta:
    domains = {Domain.METRICS}
    if kind is EvidenceType.EVENTS:
        domains.add(Domain.CHANGES)
    return ToolMeta(integration=integration, domains=frozenset(domains), evidence_type=kind)


def _client(runtime: ToolRuntime[RunContext], integration: str) -> PrometheusClient:
    return require_client(runtime, integration, PrometheusClient)


def _result(data: Any) -> tuple[str, Any]:
    return tool_payload(data), data


@integration_tool(_meta("prometheus"))
async def prom_query(expression: str, runtime: ToolRuntime[RunContext]) -> tuple[str, Any]:
    """Run an instant PromQL query.

    Args:
        expression: PromQL expression.
    """
    return _result(await _client(runtime, "prometheus").query(expression))


@integration_tool(_meta("prometheus"))
async def prom_query_range(
    expression: str, start: int, end: int, step: int, runtime: ToolRuntime[RunContext]
) -> tuple[str, Any]:
    """Run a PromQL query over a bounded Unix-seconds window.

    Args:
        expression: PromQL expression.
        start: Start in Unix seconds.
        end: End in Unix seconds.
        step: Resolution in seconds.
    """
    return _result(await _client(runtime, "prometheus").query_range(expression, start, end, step))


@integration_tool(_meta("prometheus"))
async def prom_labels(start: int, end: int, runtime: ToolRuntime[RunContext]) -> tuple[str, Any]:
    """List Prometheus label names in a bounded window.

    Args:
        start: Start in Unix seconds.
        end: End in Unix seconds.
    """
    return _result(await _client(runtime, "prometheus").labels(start, end))


@integration_tool(_meta("prometheus"))
async def prom_series(
    selector: str, start: int, end: int, runtime: ToolRuntime[RunContext]
) -> tuple[str, Any]:
    """Find metric series matching a Prometheus selector.

    Args:
        selector: Prometheus series selector.
        start: Start in Unix seconds.
        end: End in Unix seconds.
    """
    return _result(await _client(runtime, "prometheus").series(selector, start, end))


@integration_tool(_meta("prometheus", EvidenceType.EVENTS))
async def prom_alerts(runtime: ToolRuntime[RunContext]) -> tuple[str, Any]:
    """List current Prometheus alerts."""
    return _result(await _client(runtime, "prometheus").alerts())


@integration_tool(_meta("prometheus", EvidenceType.EVENTS))
async def prom_rules(runtime: ToolRuntime[RunContext]) -> tuple[str, Any]:
    """List Prometheus recording and alerting rules."""
    return _result(await _client(runtime, "prometheus").rules())


@integration_tool(_meta("prometheus", EvidenceType.TOPOLOGY))
async def prom_targets(runtime: ToolRuntime[RunContext]) -> tuple[str, Any]:
    """List active Prometheus scrape targets."""
    return _result(await _client(runtime, "prometheus").targets())


@integration_tool(_meta("mimir"))
async def mimir_query(expression: str, runtime: ToolRuntime[RunContext]) -> tuple[str, Any]:
    """Run an instant PromQL query against Mimir.

    Args:
        expression: PromQL expression.
    """
    return _result(await _client(runtime, "mimir").query(expression))


@integration_tool(_meta("mimir"))
async def mimir_query_range(
    expression: str, start: int, end: int, step: int, runtime: ToolRuntime[RunContext]
) -> tuple[str, Any]:
    """Run a Mimir PromQL query over a bounded window.

    Args:
        expression: PromQL expression.
        start: Start in Unix seconds.
        end: End in Unix seconds.
        step: Resolution in seconds.
    """
    return _result(await _client(runtime, "mimir").query_range(expression, start, end, step))


@integration_tool(_meta("mimir"))
async def mimir_labels(start: int, end: int, runtime: ToolRuntime[RunContext]) -> tuple[str, Any]:
    """List Mimir label names in a bounded window.

    Args:
        start: Start in Unix seconds.
        end: End in Unix seconds.
    """
    return _result(await _client(runtime, "mimir").labels(start, end))


@integration_tool(_meta("mimir"))
async def mimir_series(
    selector: str, start: int, end: int, runtime: ToolRuntime[RunContext]
) -> tuple[str, Any]:
    """Find Mimir metric series matching a selector.

    Args:
        selector: Prometheus series selector.
        start: Start in Unix seconds.
        end: End in Unix seconds.
    """
    return _result(await _client(runtime, "mimir").series(selector, start, end))


PROM_TOOLS: list[BaseTool] = [
    prom_query,
    prom_query_range,
    prom_labels,
    prom_series,
    prom_alerts,
    prom_rules,
    prom_targets,
]
MIMIR_TOOLS: list[BaseTool] = [mimir_query, mimir_query_range, mimir_labels, mimir_series]
