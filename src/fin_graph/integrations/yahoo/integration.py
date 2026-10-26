"""Registry adapter for Yahoo Finance: a data provider, so it carries no tools of its own."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from langchain_core.tools import BaseTool

from fin_graph.integrations.base import Integration, VerifyResult, VerifyStatus
from fin_graph.integrations.yahoo.client import YahooClient
from fin_graph.integrations.yahoo.session import YahooSession
from fin_graph.integrations.yahoo.settings import YahooSettings


class YahooIntegration(Integration[YahooSettings, YahooClient]):
    """Reached through the ``analytics`` tools when ``MARKET_DATA_PROVIDER=yahoo``."""

    id = "yahoo"
    display_name = "Yahoo Finance"

    def load_settings(self, env_file: str | None) -> YahooSettings | None:
        settings = YahooSettings(_env_file=env_file)
        return settings if settings.enabled else None

    def build_client(self, settings: YahooSettings) -> YahooClient:
        return YahooClient(
            YahooSession(timeout_seconds=settings.request_timeout_seconds),
            report_lag_days=settings.report_lag_days,
            snapshot_max_age_days=settings.snapshot_max_age_days,
        )

    def tools(self) -> list[BaseTool]:
        return []

    async def verify(self, client: YahooClient) -> VerifyResult:
        today = datetime.now(UTC).date()
        bars = await client.prices("AAPL", today - timedelta(days=10), today)
        if not bars:
            return VerifyResult(self.id, VerifyStatus.FAILED, "Yahoo returned no AAPL prices")
        return VerifyResult(
            self.id, VerifyStatus.OK, f"Yahoo prices reachable: AAPL closed at {bars[-1].close:.2f}"
        )
