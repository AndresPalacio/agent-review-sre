"""New Relic registry adapter."""

from __future__ import annotations

from langchain_core.tools import BaseTool

from fin_graph.integrations.base import Integration, VerifyResult, VerifyStatus
from fin_graph.integrations.newrelic.client import NewRelicClient
from fin_graph.integrations.newrelic.settings import NewRelicSettings
from fin_graph.integrations.newrelic.tools import TOOLS


class NewRelicIntegration(Integration[NewRelicSettings, NewRelicClient]):
    id = "newrelic"
    display_name = "New Relic"

    def load_settings(self, env_file: str | None) -> NewRelicSettings | None:
        settings = NewRelicSettings(_env_file=env_file)
        return settings if settings.enabled else None

    def build_client(self, settings: NewRelicSettings) -> NewRelicClient:
        assert settings.api_key is not None and settings.account_id is not None
        return NewRelicClient(
            settings.url,
            settings.api_key.get_secret_value(),
            settings.account_id,
            timeout_seconds=settings.request_timeout_seconds,
        )

    def tools(self) -> list[BaseTool]:
        return list(TOOLS)

    async def verify(self, client: NewRelicClient) -> VerifyResult:
        await client.account()
        return VerifyResult(self.id, VerifyStatus.OK, "New Relic account read access confirmed")
