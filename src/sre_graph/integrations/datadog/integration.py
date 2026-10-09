"""Datadog registry adapter."""

from __future__ import annotations

from langchain_core.tools import BaseTool

from sre_graph.integrations.base import Integration, VerifyResult, VerifyStatus
from sre_graph.integrations.datadog.client import DatadogClient
from sre_graph.integrations.datadog.settings import DatadogSettings
from sre_graph.integrations.datadog.tools import TOOLS


class DatadogIntegration(Integration[DatadogSettings, DatadogClient]):
    id = "datadog"
    display_name = "Datadog"

    def load_settings(self, env_file: str | None) -> DatadogSettings | None:
        settings = DatadogSettings(_env_file=env_file)
        return settings if settings.enabled else None

    def build_client(self, settings: DatadogSettings) -> DatadogClient:
        assert settings.api_key and settings.app_key
        return DatadogClient(
            settings.url,
            settings.api_key.get_secret_value(),
            settings.app_key.get_secret_value(),
            timeout_seconds=settings.request_timeout_seconds,
        )

    def tools(self) -> list[BaseTool]:
        return list(TOOLS)

    async def verify(self, client: DatadogClient) -> VerifyResult:
        await client.monitors(1)
        return VerifyResult(self.id, VerifyStatus.OK, "Datadog monitor read access confirmed")
