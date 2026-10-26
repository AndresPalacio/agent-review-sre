"""Read-only GitHub deployment, commit, PR and workflow tools."""

from __future__ import annotations

from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool

from fin_graph.domain.tools import Domain, EvidenceType, ToolMeta
from fin_graph.integrations.context import RunContext, require_client
from fin_graph.integrations.github.client import GitHubClient
from fin_graph.integrations.payload import tool_payload
from fin_graph.integrations.tooling import integration_tool


def _meta() -> ToolMeta:
    return ToolMeta(
        integration="github",
        domains=frozenset({Domain.CHANGES}),
        evidence_type=EvidenceType.CHANGES,
    )


def _client(runtime: ToolRuntime[RunContext]) -> GitHubClient:
    return require_client(runtime, "github", GitHubClient)


def _result(data: Any) -> tuple[str, Any]:
    return tool_payload(data), data


@integration_tool(_meta())
async def gh_deployments(
    start: int, end: int, runtime: ToolRuntime[RunContext], limit: int = 50, page: int = 1
) -> tuple[str, Any]:
    """Read one page of repository deployments in the incident window.

    Args:
        start: Start in Unix seconds.
        end: End in Unix seconds.
        limit: Page size up to 100.
        page: Page number starting at 1.
    """
    return _result(await _client(runtime).deployments(start, end, limit, page))


@integration_tool(_meta())
async def gh_commits(
    start: int, end: int, runtime: ToolRuntime[RunContext], limit: int = 50, page: int = 1
) -> tuple[str, Any]:
    """Read commits authored within a bounded window.

    Args:
        start: Start in Unix seconds.
        end: End in Unix seconds.
        limit: Page size up to 100.
        page: Page number starting at 1.
    """
    return _result(await _client(runtime).commits(start, end, limit, page))


@integration_tool(_meta())
async def gh_pulls(
    start: int, end: int, runtime: ToolRuntime[RunContext], limit: int = 50, page: int = 1
) -> tuple[str, Any]:
    """Read PRs updated in a bounded window, one sorted page at a time.

    Args:
        start: Start in Unix seconds.
        end: End in Unix seconds.
        limit: Page size up to 100.
        page: Page number starting at 1.
    """
    return _result(await _client(runtime).pulls(start, end, limit, page))


@integration_tool(_meta())
async def gh_workflow_runs(
    start: int, end: int, runtime: ToolRuntime[RunContext], limit: int = 50, page: int = 1
) -> tuple[str, Any]:
    """Read one page of workflow runs created in a bounded window.

    Args:
        start: Start in Unix seconds.
        end: End in Unix seconds.
        limit: Page size up to 100.
        page: Page number starting at 1.
    """
    return _result(await _client(runtime).workflow_runs(start, end, limit, page))


@integration_tool(_meta())
async def gh_compare(base: str, head: str, runtime: ToolRuntime[RunContext]) -> tuple[str, Any]:
    """Compare two repository refs for changed commits and files.

    Args:
        base: Base branch, tag or commit.
        head: Head branch, tag or commit.
    """
    return _result(await _client(runtime).compare(base, head))


TOOLS: list[BaseTool] = [gh_deployments, gh_commits, gh_pulls, gh_workflow_runs, gh_compare]
