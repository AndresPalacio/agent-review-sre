"""Registry adapter for Financial Datasets: a data provider, so it carries no tools of its own."""

from __future__ import annotations

from datetime import UTC, datetime

from langchain_core.tools import BaseTool

from fin_graph.integrations.base import Integration, VerifyResult, VerifyStatus
from fin_graph.integrations.financial_datasets.client import FinancialDatasetsClient
from fin_graph.integrations.financial_datasets.settings import FinancialDatasetsSettings


class FinancialDatasetsIntegration(Integration[FinancialDatasetsSettings, FinancialDatasetsClient]):
    """Reached through the ``analytics`` tools when ``MARKET_DATA_PROVIDER=financial_datasets``."""

    id = "financial_datasets"
    display_name = "Financial Datasets"

    def load_settings(self, env_file: str | None) -> FinancialDatasetsSettings | None:
        settings = FinancialDatasetsSettings(_env_file=env_file)
        return settings if settings.enabled else None

    def build_client(self, settings: FinancialDatasetsSettings) -> FinancialDatasetsClient:
        assert settings.api_key is not None
        return FinancialDatasetsClient(
            settings.api_key.get_secret_value(),
            url=settings.url,
            timeout_seconds=settings.request_timeout_seconds,
        )

    def tools(self) -> list[BaseTool]:
        return []

    async def verify(self, client: FinancialDatasetsClient) -> VerifyResult:
        metrics = await client.financial_metrics("AAPL", datetime.now(UTC).date(), limit=1)
        if not metrics:
            return VerifyResult(self.id, VerifyStatus.FAILED, "no AAPL metrics returned")
        return VerifyResult(self.id, VerifyStatus.OK, "Financial Datasets metrics reachable")
