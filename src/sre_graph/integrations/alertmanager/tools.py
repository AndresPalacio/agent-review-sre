"""Read-only Alertmanager tools."""

from __future__ import annotations

from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool

from sre_graph.domain.tools import Domain, EvidenceType, ToolMeta
from sre_graph.integrations.alertmanager.client import AlertmanagerClient
from sre_graph.integrations.context import RunContext, require_client
from sre_graph.integrations.payload import tool_payload
from sre_graph.integrations.tooling import integration_tool

META = ToolMeta(
    integration="alertmanager",
    domains=frozenset({Domain.METRICS}),
    evidence_type=EvidenceType.EVENTS,
)


def _client(runtime: ToolRuntime[RunContext]) -> AlertmanagerClient:
    return require_client(runtime, "alertmanager", AlertmanagerClient)


@integration_tool(META)
async def am_alerts(runtime: ToolRuntime[RunContext]) -> tuple[str, Any]:
    """List active Alertmanager alerts."""
    data = await _client(runtime).alerts()
    return tool_payload(data), data


@integration_tool(META)
async def am_silences(runtime: ToolRuntime[RunContext]) -> tuple[str, Any]:
    """List current Alertmanager silences without changing them."""
    data = await _client(runtime).silences()
    return tool_payload(data), data


TOOLS: list[BaseTool] = [am_alerts, am_silences]
