"""The built-in integrations, registered explicitly: market data first, then the SRE ones."""

from __future__ import annotations

from fin_graph.integrations.alertmanager.integration import AlertmanagerIntegration
from fin_graph.integrations.analytics.integration import (
    AnalyticsIntegration,
    MarketFixturesIntegration,
)
from fin_graph.integrations.aws.integration import AwsIntegration
from fin_graph.integrations.datadog.integration import DatadogIntegration
from fin_graph.integrations.financial_datasets.integration import FinancialDatasetsIntegration
from fin_graph.integrations.github.integration import GitHubIntegration
from fin_graph.integrations.grafana.integration import GrafanaIntegration
from fin_graph.integrations.kubernetes.integration import KubernetesIntegration
from fin_graph.integrations.loki.integration import LokiIntegration
from fin_graph.integrations.newrelic.integration import NewRelicIntegration
from fin_graph.integrations.prometheus.integration import MimirIntegration, PrometheusIntegration
from fin_graph.integrations.registry import IntegrationRegistry
from fin_graph.integrations.sentry.integration import SentryIntegration
from fin_graph.integrations.tempo.integration import TempoIntegration
from fin_graph.integrations.wave2 import wave2_integrations
from fin_graph.integrations.yahoo.integration import YahooIntegration

#: Market-data integrations that are on by default (Yahoo needs no key; analytics no config).
DEFAULT_MARKET_INTEGRATIONS = ("yahoo", "analytics")


def default_registry(env_file: str | None = ".env") -> IntegrationRegistry:
    """Every built-in integration; each stays inactive until its settings enable it.

    Yahoo and the analytics tools are the exception: they are on unless ``YAHOO_ENABLED=false``,
    and nothing reaches the network until a tool runs.
    """
    return IntegrationRegistry(
        [
            YahooIntegration(),
            FinancialDatasetsIntegration(),
            MarketFixturesIntegration(),
            AnalyticsIntegration(),
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
