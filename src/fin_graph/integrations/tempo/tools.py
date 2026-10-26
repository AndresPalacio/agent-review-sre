"""Tempo trace read tools."""

from __future__ import annotations

from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool

from fin_graph.domain.tools import Domain, EvidenceType, ToolMeta
from fin_graph.integrations.context import RunContext, require_client
from fin_graph.integrations.payload import tool_payload
from fin_graph.integrations.tempo.client import TempoClient
from fin_graph.integrations.tooling import integration_tool

META = ToolMeta(
    integration="tempo", domains=frozenset({Domain.TRACES}), evidence_type=EvidenceType.TRACES
)


def _client(runtime: ToolRuntime[RunContext]) -> TempoClient:
    return require_client(runtime, "tempo", TempoClient)


@integration_tool(META)
async def tempo_search(
    query: str, start: int, end: int, runtime: ToolRuntime[RunContext], limit: int = 20
) -> tuple[str, Any]:
    """Search Tempo traces using TraceQL in a bounded window.

    Args:
        query: TraceQL expression.
        start: Start in Unix seconds.
        end: End in Unix seconds.
        limit: Maximum trace summaries, up to 100.
    """
    data = await _client(runtime).search(query, start, end, limit)
    return tool_payload(data), data


@integration_tool(META)
async def tempo_trace(trace_id: str, runtime: ToolRuntime[RunContext]) -> tuple[str, Any]:
    """Read a Tempo trace by its hexadecimal trace ID.

    Args:
        trace_id: Trace ID with 16 or 32 hexadecimal characters.
    """
    data = await _client(runtime).trace(trace_id)
    return tool_payload(data), data


TOOLS: list[BaseTool] = [tempo_search, tempo_trace]
