from __future__ import annotations

import pytest

from sre_graph.integrations.base import VerifyStatus
from sre_graph.integrations.catalog import default_registry

IDS = (
    "kubernetes",
    "prometheus",
    "alertmanager",
    "grafana",
    "loki",
    "tempo",
    "mimir",
    "datadog",
    "newrelic",
    "aws",
    "sentry",
    "github",
    "opsgenie",
    "incident_io",
    "jira",
    "honeycomb",
    "elasticsearch",
    "opensearch",
    "splunk",
    "victoria_logs",
    "coralogix",
    "azure",
    "argocd",
    "helm",
    "gitlab",
)


def test_observability_catalog_is_explicit_and_off_by_default() -> None:
    registry = default_registry(env_file=None)
    context = registry.run_context()

    assert registry.ids == IDS
    assert context.clients == {}
    assert registry.tools(context) == []


async def test_verify_all_isolates_a_failed_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMETHEUS_ENABLED", "true")
    monkeypatch.setenv("PROMETHEUS_URL", "not-an-http-url")
    registry = default_registry(env_file=None)

    context = registry.run_context()
    results = await registry.verify_all(context)

    assert context.clients == {}
    assert "prometheus" in context.unavailable
    assert (
        next(item for item in results if item.integration == "prometheus").status
        is VerifyStatus.FAILED
    )
    assert all(
        item.status is VerifyStatus.NOT_CONFIGURED
        for item in results
        if item.integration != "prometheus"
    )
    assert registry.tools(context) == []
