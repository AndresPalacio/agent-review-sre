"""The built-in financial integrations, registered explicitly."""

from __future__ import annotations

from fin_graph.integrations.analytics.integration import (
    AnalyticsIntegration,
    MarketFixturesIntegration,
)
from fin_graph.integrations.financial_datasets.integration import FinancialDatasetsIntegration
from fin_graph.integrations.registry import IntegrationRegistry
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
        ],
        env_file=env_file,
    )
