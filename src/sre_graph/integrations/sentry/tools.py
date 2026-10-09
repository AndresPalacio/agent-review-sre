"""Read-only Sentry issue and release tools."""

from __future__ import annotations

from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool

from sre_graph.domain.tools import Domain, EvidenceType, ToolMeta
from sre_graph.integrations.context import RunContext, require_client
from sre_graph.integrations.payload import tool_payload
from sre_graph.integrations.sentry.client import SentryClient
from sre_graph.integrations.tooling import integration_tool


def _meta(domains: set[Domain], kind: EvidenceType) -> ToolMeta:
    return ToolMeta(integration="sentry", domains=frozenset(domains), evidence_type=kind)


def _client(runtime: ToolRuntime[RunContext]) -> SentryClient:
    return require_client(runtime, "sentry", SentryClient)


def _result(data: Any) -> tuple[str, Any]:
    return tool_payload(data), data


@integration_tool(_meta({Domain.ERRORS}, EvidenceType.ERRORS))
async def sentry_issues(
    query: str,
    start: int,
    end: int,
    runtime: ToolRuntime[RunContext],
    limit: int = 100,
    cursor: str | None = None,
) -> tuple[str, Any]:
    """Search one page of Sentry issues in a bounded window.

    Args:
        query: Sentry issue search query.
        start: Start in Unix seconds.
        end: End in Unix seconds.
        limit: Page size up to 100.
        cursor: Cursor from the previous page, if any.
    """
    return _result(await _client(runtime).issues(query, start, end, limit, cursor))


@integration_tool(_meta({Domain.ERRORS}, EvidenceType.ERRORS))
async def sentry_issue(issue_id: str, runtime: ToolRuntime[RunContext]) -> tuple[str, Any]:
    """Read one Sentry issue with its latest event summary.

    Args:
        issue_id: Issue ID.
    """
    return _result(await _client(runtime).issue_detail(issue_id))


@integration_tool(_meta({Domain.ERRORS}, EvidenceType.EVENTS))
async def sentry_events(
    issue_id: str,
    start: int,
    end: int,
    runtime: ToolRuntime[RunContext],
    limit: int = 100,
    cursor: str | None = None,
) -> tuple[str, Any]:
    """Read one page of events for a Sentry issue.

    Args:
        issue_id: Issue ID.
        start: Start in Unix seconds.
        end: End in Unix seconds.
        limit: Page size up to 100.
        cursor: Cursor from the previous page, if any.
    """
    return _result(await _client(runtime).events(issue_id, start, end, limit, cursor))


@integration_tool(_meta({Domain.CHANGES}, EvidenceType.CHANGES))
async def sentry_releases(
    runtime: ToolRuntime[RunContext], limit: int = 100, cursor: str | None = None
) -> tuple[str, Any]:
    """Read one page of recent Sentry releases.

    Args:
        limit: Page size up to 100.
        cursor: Cursor from the previous page, if any.
    """
    return _result(await _client(runtime).releases(limit, cursor))


@integration_tool(_meta({Domain.ERRORS}, EvidenceType.ERRORS))
async def sentry_event_detail(
    issue_id: str, event_id: str, runtime: ToolRuntime[RunContext]
) -> tuple[str, Any]:
    """Read an issue event with exception and stacktrace entries.

    Args:
        issue_id: Issue ID.
        event_id: Event ID from sentry_events.
    """
    return _result(await _client(runtime).event_detail(issue_id, event_id))


TOOLS: list[BaseTool] = [
    sentry_issues,
    sentry_issue,
    sentry_events,
    sentry_event_detail,
    sentry_releases,
]
