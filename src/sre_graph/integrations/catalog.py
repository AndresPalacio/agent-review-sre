"""The built-in integrations, registered explicitly."""

from __future__ import annotations

from sre_graph.integrations.alertmanager.integration import AlertmanagerIntegration
from sre_graph.integrations.aws.integration import AwsIntegration
from sre_graph.integrations.datadog.integration import DatadogIntegration
from sre_graph.integrations.github.integration import GitHubIntegration
from sre_graph.integrations.grafana.integration import GrafanaIntegration
from sre_graph.integrations.kubernetes.integration import KubernetesIntegration
from sre_graph.integrations.loki.integration import LokiIntegration
from sre_graph.integrations.newrelic.integration import NewRelicIntegration
from sre_graph.integrations.prometheus.integration import MimirIntegration, PrometheusIntegration
from sre_graph.integrations.registry import IntegrationRegistry
from sre_graph.integrations.sentry.integration import SentryIntegration
from sre_graph.integrations.tempo.integration import TempoIntegration
from sre_graph.integrations.wave2 import wave2_integrations


def default_registry(env_file: str | None = ".env") -> IntegrationRegistry:
    """Every built-in integration; each stays inactive until its settings enable it."""
    return IntegrationRegistry(
        [
            KubernetesIntegration(),
            PrometheusIntegration(),
            AlertmanagerIntegration(),
            GrafanaIntegration(),
            LokiIntegration(),
            TempoIntegration(),
            MimirIntegration(),
            DatadogIntegration(),
            NewRelicIntegration(),
            AwsIntegration(),
            SentryIntegration(),
            GitHubIntegration(),
            *wave2_integrations(),
        ],
        env_file=env_file,
    )
