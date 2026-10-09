from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sre_graph.integrations.base import IntegrationError, VerifyResult, VerifyStatus
from sre_graph.integrations.catalog import default_registry
from sre_graph.integrations.kubernetes.client import KubernetesClient
from sre_graph.integrations.kubernetes.integration import KubernetesIntegration
from sre_graph.integrations.kubernetes.settings import KubernetesSettings


def test_settings_default_to_disabled() -> None:
    settings = KubernetesSettings(_env_file=None)

    assert settings.enabled is False
    assert settings.verify_namespace == "default"
    assert settings.max_items == 200


def test_settings_read_k8s_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("K8S_ENABLED", "true")
    monkeypatch.setenv("K8S_CONTEXT", "staging")
    monkeypatch.setenv("K8S_KUBECONFIG", "")

    settings = KubernetesSettings(_env_file=None)

    assert settings.enabled is True
    assert settings.context == "staging"
    assert settings.kubeconfig is None


def test_settings_reject_a_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="request_timeout_seconds"):
        KubernetesSettings(_env_file=None, request_timeout_seconds=0)


def test_a_disabled_integration_is_not_configured() -> None:
    assert KubernetesIntegration().load_settings(None) is None


def test_an_enabled_integration_builds_a_client(
    monkeypatch: pytest.MonkeyPatch, kubeconfig_file: Path
) -> None:
    monkeypatch.setenv("K8S_ENABLED", "true")
    monkeypatch.setenv("K8S_KUBECONFIG", str(kubeconfig_file))
    integration = KubernetesIntegration()

    settings = integration.load_settings(None)

    assert settings is not None
    assert integration.build_client(settings).host == "https://k8s.test:6443"


async def test_verify_reports_version_and_pod_access(
    kube_client: KubernetesClient, kube_rest: Any
) -> None:
    result = await KubernetesIntegration().verify(kube_client)

    assert result == VerifyResult(
        "kubernetes",
        VerifyStatus.OK,
        "Kubernetes v1.31.2 at https://k8s.test:6443; can list pods in payments",
    )
    assert kube_rest.query("/api/v1/namespaces/payments/pods")["limit"] == ["1"]


async def test_verify_fails_when_the_credentials_cannot_list_pods(
    kube_client: KubernetesClient, kube_rest: Any
) -> None:
    kube_rest.fail("/api/v1/namespaces/payments/pods", 401, "Unauthorized", "Unauthorized")

    with pytest.raises(IntegrationError, match="401"):
        await KubernetesIntegration().verify(kube_client)


async def test_a_missing_kubeconfig_makes_kubernetes_unavailable_not_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("K8S_ENABLED", "true")
    monkeypatch.setenv("K8S_KUBECONFIG", str(tmp_path / "missing.yaml"))
    registry = default_registry(env_file=None)

    context = registry.run_context()
    result = next(
        item for item in await registry.verify_all(context) if item.integration == "kubernetes"
    )

    assert context.clients == {}
    assert result.status is VerifyStatus.FAILED
    assert "missing.yaml" in result.detail
    assert registry.tools(context) == []


def test_the_default_registry_offers_kubernetes_tools_once_enabled(
    monkeypatch: pytest.MonkeyPatch, kubeconfig_file: Path
) -> None:
    monkeypatch.setenv("K8S_ENABLED", "true")
    monkeypatch.setenv("K8S_KUBECONFIG", str(kubeconfig_file))
    registry = default_registry(env_file=None)

    tools = registry.tools(registry.run_context())

    assert registry.ids == (
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
    assert len(tools) == 9
