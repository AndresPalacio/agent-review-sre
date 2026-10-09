"""Read-only New Relic NRQL, issue, entity and APM error tools."""

from __future__ import annotations

from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool

from sre_graph.domain.tools import Domain, EvidenceType, ToolMeta
from sre_graph.integrations.context import RunContext, require_client
from sre_graph.integrations.newrelic.client import NewRelicClient
from sre_graph.integrations.payload import tool_payload
from sre_graph.integrations.tooling import integration_tool


def _meta(domains: set[Domain], kind: EvidenceType) -> ToolMeta:
    return ToolMeta(integration="newrelic", domains=frozenset(domains), evidence_type=kind)


def _client(runtime: ToolRuntime[RunContext]) -> NewRelicClient:
    return require_client(runtime, "newrelic", NewRelicClient)


def _result(data: Any) -> tuple[str, Any]:
    return tool_payload(data), data


@integration_tool(_meta({Domain.METRICS, Domain.LOGS}, EvidenceType.METRICS))
async def nr_nrql(query: str, runtime: ToolRuntime[RunContext]) -> tuple[str, Any]:
    """Run a read-only New Relic NRQL SELECT query.

    Args:
        query: One NRQL SELECT query.
    """
    return _result(await _client(runtime).nrql(query))


@integration_tool(_meta({Domain.ERRORS}, EvidenceType.EVENTS))
async def nr_issues(runtime: ToolRuntime[RunContext], cursor: str | None = None) -> tuple[str, Any]:
    """List one page of New Relic issues from the last 24 hours.

    Args:
        cursor: Cursor from the previous page, if any.
    """
    return _result(await _client(runtime).issues(cursor))


@integration_tool(_meta({Domain.ERRORS}, EvidenceType.EVENTS))
async def nr_incidents(
    runtime: ToolRuntime[RunContext], cursor: str | None = None
) -> tuple[str, Any]:
    """List one page of New Relic alert incidents from the last 24 hours.

    Args:
        cursor: Cursor from the previous page, if any.
    """
    return _result(await _client(runtime).incidents(cursor))


@integration_tool(_meta({Domain.METRICS, Domain.TRACES}, EvidenceType.TOPOLOGY))
async def nr_entities(
    search: str, runtime: ToolRuntime[RunContext], cursor: str | None = None
) -> tuple[str, Any]:
    """Find New Relic entities using an entity search expression.

    Args:
        search: New Relic entity search expression.
        cursor: Cursor from the previous page, if any.
    """
    return _result(await _client(runtime).entities(search, cursor))


@integration_tool(_meta({Domain.ERRORS, Domain.TRACES}, EvidenceType.ERRORS))
async def nr_apm_errors(
    service: str, start: int, end: int, runtime: ToolRuntime[RunContext], limit: int = 100
) -> tuple[str, Any]:
    """Read APM TransactionError events for a service in a bounded window.

    Args:
        service: Application name; empty for all applications.
        start: Start in Unix seconds.
        end: End in Unix seconds.
        limit: Maximum error events, up to 100.
    """
    return _result(await _client(runtime).apm_errors(service, start, end, limit))


TOOLS: list[BaseTool] = [nr_nrql, nr_issues, nr_incidents, nr_entities, nr_apm_errors]
