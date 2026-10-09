"""Loki read tools."""

from __future__ import annotations

from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool

from sre_graph.domain.tools import Domain, EvidenceType, ToolMeta
from sre_graph.integrations.context import RunContext, require_client
from sre_graph.integrations.loki.client import LokiClient
from sre_graph.integrations.payload import tool_payload
from sre_graph.integrations.tooling import integration_tool

META = ToolMeta(
    integration="loki", domains=frozenset({Domain.LOGS}), evidence_type=EvidenceType.LOGS
)


def _client(runtime: ToolRuntime[RunContext]) -> LokiClient:
    return require_client(runtime, "loki", LokiClient)


@integration_tool(META)
async def loki_query_range(
    query: str, start: int, end: int, runtime: ToolRuntime[RunContext], limit: int = 100
) -> tuple[str, Any]:
    """Search Loki logs with LogQL in a bounded window.

    Args:
        query: LogQL expression with a stream selector.
        start: Start in Unix seconds.
        end: End in Unix seconds.
        limit: Maximum log entries, up to 1000.
    """
    data = await _client(runtime).query_range(query, start, end, limit)
    return tool_payload(data), data


@integration_tool(META)
async def loki_labels(start: int, end: int, runtime: ToolRuntime[RunContext]) -> tuple[str, Any]:
    """List Loki label names in a bounded window.

    Args:
        start: Start in Unix seconds.
        end: End in Unix seconds.
    """
    data = await _client(runtime).labels(start, end)
    return tool_payload(data), data


TOOLS: list[BaseTool] = [loki_query_range, loki_labels]
