"""Grafana registry adapter."""

from __future__ import annotations

from langchain_core.tools import BaseTool

from sre_graph.integrations.base import Integration, VerifyResult, VerifyStatus
from sre_graph.integrations.grafana.client import GrafanaClient
from sre_graph.integrations.grafana.settings import GrafanaSettings
from sre_graph.integrations.grafana.tools import TOOLS


class GrafanaIntegration(Integration[GrafanaSettings, GrafanaClient]):
    id = "grafana"
    display_name = "Grafana"

    def load_settings(self, env_file: str | None) -> GrafanaSettings | None:
        settings = GrafanaSettings(_env_file=env_file)
        return settings if settings.enabled else None

    def build_client(self, settings: GrafanaSettings) -> GrafanaClient:
        assert settings.url is not None
        return GrafanaClient(
            settings.url,
            token=settings.token.get_secret_value() if settings.token else None,
            timeout_seconds=settings.request_timeout_seconds,
        )

    def tools(self) -> list[BaseTool]:
        return list(TOOLS)

    async def verify(self, client: GrafanaClient) -> VerifyResult:
        await client.dashboards(limit=1)
        return VerifyResult(self.id, VerifyStatus.OK, "Grafana dashboard read access confirmed")
