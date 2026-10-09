"""Grafana read tools."""

from __future__ import annotations

from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool

from sre_graph.domain.tools import Domain, EvidenceType, ToolMeta
from sre_graph.integrations.context import RunContext, require_client
from sre_graph.integrations.grafana.client import GrafanaClient
from sre_graph.integrations.payload import tool_payload
from sre_graph.integrations.tooling import integration_tool


def _client(runtime: ToolRuntime[RunContext]) -> GrafanaClient:
    return require_client(runtime, "grafana", GrafanaClient)


@integration_tool(
    ToolMeta(
        integration="grafana",
        domains=frozenset({Domain.METRICS}),
        evidence_type=EvidenceType.EVENTS,
    )
)
async def grafana_alert_rules(runtime: ToolRuntime[RunContext]) -> tuple[str, Any]:
    """List Grafana unified alerting rules."""
    data = await _client(runtime).alert_rules()
    return tool_payload(data), data


@integration_tool(
    ToolMeta(
        integration="grafana",
        domains=frozenset({Domain.CHANGES}),
        evidence_type=EvidenceType.CHANGES,
    )
)
async def grafana_annotations(
    start: int, end: int, runtime: ToolRuntime[RunContext]
) -> tuple[str, Any]:
    """List Grafana annotations in a bounded Unix-seconds window.

    Args:
        start: Start in Unix seconds.
        end: End in Unix seconds.
    """
    data = await _client(runtime).annotations(start, end)
    return tool_payload(data), data


@integration_tool(
    ToolMeta(
        integration="grafana",
        domains=frozenset({Domain.METRICS}),
        evidence_type=EvidenceType.TOPOLOGY,
    )
)
async def grafana_dashboards(query: str, runtime: ToolRuntime[RunContext]) -> tuple[str, Any]:
    """Search Grafana dashboards by title.

    Args:
        query: Dashboard title search text.
    """
    data = await _client(runtime).dashboards(query)
    return tool_payload(data), data


TOOLS: list[BaseTool] = [grafana_alert_rules, grafana_annotations, grafana_dashboards]
