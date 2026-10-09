from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.tools import BaseTool, tool

from sre_graph.domain.tools import Domain, EvidenceType, ToolMeta
from sre_graph.integrations.alertmanager.integration import AlertmanagerIntegration
from sre_graph.integrations.base import Integration, VerifyResult, VerifyStatus
from sre_graph.integrations.context import RunContext
from sre_graph.integrations.datadog.integration import DatadogIntegration
from sre_graph.integrations.grafana.integration import GrafanaIntegration
from sre_graph.integrations.loki.integration import LokiIntegration
from sre_graph.integrations.newrelic.integration import NewRelicIntegration
from sre_graph.integrations.prometheus.integration import MimirIntegration, PrometheusIntegration
from sre_graph.integrations.registry import IntegrationRegistry
from sre_graph.integrations.tempo.integration import TempoIntegration
from sre_graph.integrations.tooling import integration_tool

# Built at runtime so this file holds no string that looks like a real credential.
GITHUB_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


def _meta(integration: str, *domains: Domain) -> ToolMeta:
    return ToolMeta(
        integration=integration, domains=frozenset(domains), evidence_type=EvidenceType.LOGS
    )


@integration_tool(_meta("logs", Domain.LOGS))
async def logs_query(query: str) -> tuple[str, Any]:
    """Run a log query.

    Args:
        query: The query text.
    """
    return query, None


@integration_tool(_meta("metrics", Domain.METRICS))
async def metrics_query(query: str) -> tuple[str, Any]:
    """Run a metrics query.

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


class LogsIntegration(FakeIntegration):
    id = "logs"
    display_name = "Logs"


class MetricsIntegration(FakeIntegration):
    id = "metrics"
    display_name = "Metrics"


def test_two_integrations_with_one_id_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate integration id: logs"):
        IntegrationRegistry([LogsIntegration(), LogsIntegration()], env_file=None)


def test_only_configured_integrations_get_a_client() -> None:
    registry = IntegrationRegistry(
        [LogsIntegration(), MetricsIntegration(configured=False)], env_file=None
    )

    context = registry.run_context()

    assert list(context.clients) == ["logs"]
    assert context.unavailable == {}


def test_an_integration_that_fails_to_build_is_unavailable_with_a_redacted_reason() -> None:
    broken = MetricsIntegration(build_error=RuntimeError(f"bad token {GITHUB_TOKEN}"))
    registry = IntegrationRegistry([LogsIntegration(), broken], env_file=None)

    context = registry.run_context()

    assert list(context.clients) == ["logs"]
    assert context.unavailable == {"metrics": "RuntimeError: bad token [REDACTED:github_token]"}


@pytest.mark.parametrize(
    "integration,env,missing",
    [
        (PrometheusIntegration(), "PROMETHEUS", "url"),
        (MimirIntegration(), "MIMIR", "url"),
        (AlertmanagerIntegration(), "ALERTMANAGER", "url"),
        (GrafanaIntegration(), "GRAFANA", "url"),
        (LokiIntegration(), "LOKI", "url"),
        (TempoIntegration(), "TEMPO", "url"),
        (NewRelicIntegration(), "NEWRELIC", "account_id"),
        (DatadogIntegration(), "DATADOG", "app_key"),
    ],
)
async def test_settings_validation_never_exposes_configured_secrets(
    monkeypatch: pytest.MonkeyPatch,
    integration: Integration[Any, Any],
    env: str,
    missing: str,
) -> None:
    marker = "QwErTy1234567890"
    secret = f"private-{env.lower()}-key-" + marker * 5
    monkeypatch.setenv(f"{env}_ENABLED", "true")
    monkeypatch.setenv(f"{env}_{'API_KEY' if env in ('NEWRELIC', 'DATADOG') else 'TOKEN'}", secret)
    monkeypatch.delenv(f"{env}_{missing.upper()}", raising=False)
    registry = IntegrationRegistry([integration], env_file=None)

    context = registry.run_context()
    [check] = await registry.verify_all(context)

    assert integration.id in context.unavailable
    assert "ValidationError" in context.unavailable[integration.id]
    assert secret not in context.unavailable[integration.id]
    assert marker not in context.unavailable[integration.id]
    assert check.status is VerifyStatus.FAILED
    assert secret not in check.detail
    assert marker not in check.detail


async def test_datadog_app_key_is_not_exposed_when_site_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "AsDfGh1234567890"
    monkeypatch.setenv("DATADOG_ENABLED", "true")
    monkeypatch.setenv("DATADOG_SITE", "invalid.example")
    monkeypatch.setenv("DATADOG_API_KEY", "api-" + marker * 5)
    monkeypatch.setenv("DATADOG_APP_KEY", "app-" + marker * 5)
    context = IntegrationRegistry([DatadogIntegration()], env_file=None).run_context()

    assert "datadog" in context.unavailable
    assert marker not in context.unavailable["datadog"]


def test_tools_come_only_from_integrations_with_a_client() -> None:
    registry = IntegrationRegistry(
        [
            LogsIntegration(tools=[logs_query]),
            MetricsIntegration(configured=False, tools=[metrics_query]),
        ],
        env_file=None,
    )

    tools = registry.tools(registry.run_context())

    assert [candidate.name for candidate in tools] == ["logs_query"]


def test_tools_can_be_filtered_by_domain() -> None:
    registry = IntegrationRegistry(
        [LogsIntegration(tools=[logs_query]), MetricsIntegration(tools=[metrics_query])],
        env_file=None,
    )

    tools = registry.tools(registry.run_context(), domains={Domain.METRICS})

    assert [candidate.name for candidate in tools] == ["metrics_query"]


def test_a_tool_without_metadata_is_a_programming_error() -> None:
    @tool
    def bare(query: str) -> str:
        """No metadata."""
        return query

    registry = IntegrationRegistry([LogsIntegration(tools=[bare])], env_file=None)

    with pytest.raises(ValueError, match="bare carries no ToolMeta"):
        registry.tools(registry.run_context())


def test_two_tools_with_one_name_are_rejected() -> None:
    registry = IntegrationRegistry(
        [LogsIntegration(tools=[logs_query]), MetricsIntegration(tools=[logs_query])],
        env_file=None,
    )

    with pytest.raises(ValueError, match="duplicate tool name: logs_query"):
        registry.tools(registry.run_context())


async def test_verify_reports_every_integration_in_order() -> None:
    registry = IntegrationRegistry(
        [LogsIntegration(), MetricsIntegration(configured=False)], env_file=None
    )

    results = await registry.verify_all(registry.run_context())

    assert results == [
        VerifyResult("logs", VerifyStatus.OK, "reachable"),
        VerifyResult("metrics", VerifyStatus.NOT_CONFIGURED, "not configured"),
    ]


async def test_verify_reports_why_a_client_could_not_be_built() -> None:
    registry = IntegrationRegistry(
        [LogsIntegration(build_error=FileNotFoundError("no kubeconfig at /x"))], env_file=None
    )

    [result] = await registry.verify_all(registry.run_context())

    assert result == VerifyResult(
        "logs", VerifyStatus.FAILED, "FileNotFoundError: no kubeconfig at /x"
    )


async def test_a_failing_check_is_reported_not_raised() -> None:
    registry = IntegrationRegistry(
        [LogsIntegration(verify_error=ConnectionError(f"refused, token {GITHUB_TOKEN}"))],
        env_file=None,
    )

    [result] = await registry.verify_all(registry.run_context())

    assert result.status is VerifyStatus.FAILED
    assert result.detail == "refused, token [REDACTED:github_token]"


async def test_a_check_that_hangs_times_out() -> None:
    registry = IntegrationRegistry([LogsIntegration(verify_delay=5)], env_file=None)

    [result] = await registry.verify_all(registry.run_context(), timeout_seconds=0.05)

    assert result == VerifyResult("logs", VerifyStatus.FAILED, "no answer within 0.05 seconds")


def test_an_empty_context_offers_no_tools() -> None:
    registry = IntegrationRegistry([LogsIntegration(tools=[logs_query])], env_file=None)

    assert registry.tools(RunContext()) == []
