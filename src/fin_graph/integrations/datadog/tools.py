"""Datadog read-only tools for logs, metrics, changes and traces."""

from __future__ import annotations

from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool

from fin_graph.domain.tools import Domain, EvidenceType, ToolMeta
from fin_graph.integrations.context import RunContext, require_client
from fin_graph.integrations.datadog.client import DatadogClient
from fin_graph.integrations.payload import tool_payload
from fin_graph.integrations.tooling import integration_tool


def _meta(domains: set[Domain], kind: EvidenceType) -> ToolMeta:
    return ToolMeta(integration="datadog", domains=frozenset(domains), evidence_type=kind)


def _client(runtime: ToolRuntime[RunContext]) -> DatadogClient:
    return require_client(runtime, "datadog", DatadogClient)


def _result(data: Any) -> tuple[str, Any]:
    return tool_payload(data), data


@integration_tool(_meta({Domain.LOGS}, EvidenceType.LOGS))
async def dd_logs(
    query: str,
    start: int,
    end: int,
    runtime: ToolRuntime[RunContext],
    limit: int = 100,
    cursor: str | None = None,
) -> tuple[str, Any]:
    """Search one page of Datadog logs in a bounded window.

    Args:
        query: Datadog log search query.
        start: Start in Unix seconds.
        end: End in Unix seconds.
        limit: Page size, up to 100.
        cursor: Cursor from the previous page, if any.
    """
    return _result(await _client(runtime).logs(query, start, end, limit, cursor))


@integration_tool(_meta({Domain.METRICS}, EvidenceType.METRICS))
async def dd_metrics(
    query: str, start: int, end: int, runtime: ToolRuntime[RunContext]
) -> tuple[str, Any]:
    """Query Datadog timeseries metrics.

    Args:
        query: Datadog metrics query.
        start: Start in Unix seconds.
        end: End in Unix seconds.
    """
    return _result(await _client(runtime).metrics(query, start, end))


@integration_tool(_meta({Domain.METRICS}, EvidenceType.EVENTS))
async def dd_monitors(runtime: ToolRuntime[RunContext], limit: int = 100) -> tuple[str, Any]:
    """List one page of Datadog monitors.

    Args:
        limit: Page size, up to 100.
    """
    return _result(await _client(runtime).monitors(limit))


@integration_tool(_meta({Domain.CHANGES}, EvidenceType.CHANGES))
async def dd_events(
    query: str, start: int, end: int, runtime: ToolRuntime[RunContext], limit: int = 100
) -> tuple[str, Any]:
    """Search Datadog events in a bounded window.

    Args:
        query: Datadog event search query.
        start: Start in Unix seconds.
        end: End in Unix seconds.
        limit: Page size, up to 100.
    """
    return _result(await _client(runtime).events(query, start, end, limit))


@integration_tool(_meta({Domain.TRACES, Domain.ERRORS}, EvidenceType.TRACES))
async def dd_spans(
    query: str,
    start: int,
    end: int,
    runtime: ToolRuntime[RunContext],
    limit: int = 100,
    cursor: str | None = None,
) -> tuple[str, Any]:
    """Search one page of Datadog APM spans in a bounded window.

    Args:
        query: Datadog span search query.
        start: Start in Unix seconds.
        end: End in Unix seconds.
        limit: Page size, up to 100.
        cursor: Cursor from the previous page, if any.
    """
    return _result(await _client(runtime).spans(query, start, end, limit, cursor))


@integration_tool(_meta({Domain.CHANGES}, EvidenceType.EVENTS))
async def dd_incidents(runtime: ToolRuntime[RunContext], limit: int = 100) -> tuple[str, Any]:
    """List one page of Datadog incidents.

    Args:
        limit: Page size, up to 100.
    """
    return _result(await _client(runtime).incidents(limit))


TOOLS: list[BaseTool] = [dd_logs, dd_metrics, dd_monitors, dd_events, dd_spans, dd_incidents]
