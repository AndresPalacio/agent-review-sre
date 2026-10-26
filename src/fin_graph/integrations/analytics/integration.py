"""Registry adapters for the analytics tools and for offline market fixtures."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from langchain_core.tools import BaseTool

from fin_graph.config.settings import Settings
from fin_graph.integrations.analytics.engine import AnalyticsEngine
from fin_graph.integrations.analytics.tools import ANALYTICS_TOOLS
from fin_graph.integrations.base import Integration, VerifyResult, VerifyStatus
from fin_graph.integrations.financial_datasets.settings import FinancialDatasetsSettings
from fin_graph.integrations.market_data import StaticMarketData, load_market_fixtures
from fin_graph.integrations.yahoo.settings import YahooSettings


class AnalyticsIntegration(Integration[Settings, AnalyticsEngine]):
    """On whenever the provider ``MARKET_DATA_PROVIDER`` names is on, so its tools can answer."""

    id = "analytics"
    display_name = "Market analytics"

    def load_settings(self, env_file: str | None) -> Settings | None:
        settings = Settings(_env_file=env_file)
        provider = settings.market_data_provider
        if provider == "yahoo":
            enabled = YahooSettings(_env_file=env_file).enabled
        elif provider == "financial_datasets":
            enabled = FinancialDatasetsSettings(_env_file=env_file).enabled
        else:
            enabled = bool(settings.market_fixtures_dir)
        return settings if enabled else None

    def build_client(self, settings: Settings) -> AnalyticsEngine:
        return AnalyticsEngine(
            settings.market_data_provider, risk_free_rate=settings.risk_free_rate
        )

    def tools(self) -> list[BaseTool]:
        return list(ANALYTICS_TOOLS)

    async def verify(self, client: AnalyticsEngine) -> VerifyResult:
        return VerifyResult(
            self.id, VerifyStatus.OK, f"deterministic analysts over {client.data_integration}"
        )


class MarketFixturesIntegration(Integration[Settings, StaticMarketData]):
    """Recorded market data from ``MARKET_FIXTURES_DIR``, used when the provider is ``fixtures``."""

    id = "market_fixtures"
    display_name = "Market fixtures (offline)"

    def load_settings(self, env_file: str | None) -> Settings | None:
        settings = Settings(_env_file=env_file)
        return settings if settings.market_data_provider == "fixtures" else None

    def build_client(self, settings: Settings) -> StaticMarketData:
        if not settings.market_fixtures_dir:
            raise ValueError("MARKET_FIXTURES_DIR is required when MARKET_DATA_PROVIDER=fixtures")
        return load_market_fixtures(Path(settings.market_fixtures_dir))

    def tools(self) -> list[BaseTool]:
        return []

    async def verify(self, client: StaticMarketData) -> VerifyResult:
        tickers = client.tickers
        if not tickers:
            return VerifyResult(self.id, VerifyStatus.FAILED, "the fixtures directory is empty")
        today = datetime.now(UTC).date()
        metrics = await client.financial_metrics(tickers[0], today, limit=1)
        detail = f"{len(tickers)} tickers recorded ({', '.join(tickers[:5])})"
        return VerifyResult(self.id, VerifyStatus.OK, detail + ("" if metrics else "; no metrics"))
