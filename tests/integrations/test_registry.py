from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.tools import BaseTool, tool

from fin_graph.domain.tools import Domain, EvidenceType, ToolMeta
from fin_graph.integrations.base import Integration, VerifyResult, VerifyStatus
from fin_graph.integrations.context import RunContext
from fin_graph.integrations.registry import IntegrationRegistry
from fin_graph.integrations.tooling import integration_tool

# Built at runtime so this file holds no string that looks like a real credential.
GITHUB_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


def _meta(integration: str, *domains: Domain) -> ToolMeta:
    return ToolMeta(
        integration=integration, domains=frozenset(domains), evidence_type=EvidenceType.FINANCIALS
    )


@integration_tool(_meta("fundamentals", Domain.FUNDAMENTALS))
async def research_query(query: str) -> tuple[str, Any]:
    """Run a financial research query.

    Args:
        query: The query text.
    """
    return query, None


@integration_tool(_meta("technicals", Domain.TECHNICALS))
async def fundamentals_query(query: str) -> tuple[str, Any]:
    """Run a fundamentals query.

    Args:
        query: The query text.
    """
    return query, None


class FakeClient:
    pass


class FakeIntegration(Integration[str, FakeClient]):
    """Configured unless told otherwise; ``verify`` does what the test asks."""

    def __init__(
        self,
        *,
        configured: bool = True,
        build_error: Exception | None = None,
        tools: list[BaseTool] | None = None,
        verify_error: Exception | None = None,
        verify_delay: float = 0.0,
    ) -> None:
        self._configured = configured
        self._build_error = build_error
        self._tools = tools or []
        self._verify_error = verify_error
        self._verify_delay = verify_delay

    def load_settings(self, env_file: str | None) -> str | None:
        return "settings" if self._configured else None

    def build_client(self, settings: str) -> FakeClient:
        if self._build_error is not None:
            raise self._build_error
        return FakeClient()

    def tools(self) -> list[BaseTool]:
        return self._tools

    async def verify(self, client: FakeClient) -> VerifyResult:
        await asyncio.sleep(self._verify_delay)
        if self._verify_error is not None:
            raise self._verify_error
        return VerifyResult(self.id, VerifyStatus.OK, "reachable")


class ResearchIntegration(FakeIntegration):
    id = "research"
    display_name = "Research"


class FundamentalsIntegration(FakeIntegration):
    id = "fundamentals"
    display_name = "Fundamentals"


def test_two_integrations_with_one_id_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate integration id: research"):
        IntegrationRegistry([ResearchIntegration(), ResearchIntegration()], env_file=None)


def test_only_configured_integrations_get_a_client() -> None:
    registry = IntegrationRegistry(
        [ResearchIntegration(), FundamentalsIntegration(configured=False)], env_file=None
    )

    context = registry.run_context()

    assert list(context.clients) == ["research"]
    assert context.unavailable == {}


def test_an_integration_that_fails_to_build_is_unavailable_with_a_redacted_reason() -> None:
    broken = FundamentalsIntegration(build_error=RuntimeError(f"bad token {GITHUB_TOKEN}"))
    registry = IntegrationRegistry([ResearchIntegration(), broken], env_file=None)

    context = registry.run_context()

    assert list(context.clients) == ["research"]
    assert context.unavailable == {
        "fundamentals": "RuntimeError: bad token [REDACTED:github_token]"
    }


def test_tools_come_only_from_integrations_with_a_client() -> None:
    registry = IntegrationRegistry(
        [
            ResearchIntegration(tools=[research_query]),
            FundamentalsIntegration(configured=False, tools=[fundamentals_query]),
        ],
        env_file=None,
    )

    tools = registry.tools(registry.run_context())

    assert [candidate.name for candidate in tools] == ["research_query"]


def test_tools_can_be_filtered_by_domain() -> None:
    registry = IntegrationRegistry(
        [
            ResearchIntegration(tools=[research_query]),
            FundamentalsIntegration(tools=[fundamentals_query]),
        ],
        env_file=None,
    )

    tools = registry.tools(registry.run_context(), domains={Domain.TECHNICALS})

    assert [candidate.name for candidate in tools] == ["fundamentals_query"]


def test_a_tool_without_metadata_is_a_programming_error() -> None:
    @tool
    def bare(query: str) -> str:
        """No metadata."""
        return query

    registry = IntegrationRegistry([ResearchIntegration(tools=[bare])], env_file=None)

    with pytest.raises(ValueError, match="bare carries no ToolMeta"):
        registry.tools(registry.run_context())


def test_two_tools_with_one_name_are_rejected() -> None:
    registry = IntegrationRegistry(
        [
            ResearchIntegration(tools=[research_query]),
            FundamentalsIntegration(tools=[research_query]),
        ],
        env_file=None,
    )

    with pytest.raises(ValueError, match="duplicate tool name: research_query"):
        registry.tools(registry.run_context())


async def test_verify_reports_every_integration_in_order() -> None:
    registry = IntegrationRegistry(
        [ResearchIntegration(), FundamentalsIntegration(configured=False)], env_file=None
    )

    results = await registry.verify_all(registry.run_context())

    assert results == [
        VerifyResult("research", VerifyStatus.OK, "reachable"),
        VerifyResult("fundamentals", VerifyStatus.NOT_CONFIGURED, "not configured"),
    ]


async def test_verify_reports_why_a_client_could_not_be_built() -> None:
    registry = IntegrationRegistry(
        [ResearchIntegration(build_error=FileNotFoundError("no market data config at /x"))],
        env_file=None,
    )

    [result] = await registry.verify_all(registry.run_context())

    assert result == VerifyResult(
        "research", VerifyStatus.FAILED, "FileNotFoundError: no market data config at /x"
    )


async def test_a_failing_check_is_reported_not_raised() -> None:
    registry = IntegrationRegistry(
        [ResearchIntegration(verify_error=ConnectionError(f"refused, token {GITHUB_TOKEN}"))],
        env_file=None,
    )

    [result] = await registry.verify_all(registry.run_context())

    assert result.status is VerifyStatus.FAILED
    assert result.detail == "refused, token [REDACTED:github_token]"


async def test_a_check_that_hangs_times_out() -> None:
    registry = IntegrationRegistry([ResearchIntegration(verify_delay=5)], env_file=None)

    [result] = await registry.verify_all(registry.run_context(), timeout_seconds=0.05)

    assert result == VerifyResult("research", VerifyStatus.FAILED, "no answer within 0.05 seconds")


def test_an_empty_context_offers_no_tools() -> None:
    registry = IntegrationRegistry([ResearchIntegration(tools=[research_query])], env_file=None)

    assert registry.tools(RunContext()) == []
