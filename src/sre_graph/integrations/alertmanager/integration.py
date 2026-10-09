"""Alertmanager registry adapter."""

from __future__ import annotations

from langchain_core.tools import BaseTool

from sre_graph.integrations.alertmanager.client import AlertmanagerClient
from sre_graph.integrations.alertmanager.settings import AlertmanagerSettings
from sre_graph.integrations.alertmanager.tools import TOOLS
from sre_graph.integrations.base import Integration, VerifyResult, VerifyStatus


class AlertmanagerIntegration(Integration[AlertmanagerSettings, AlertmanagerClient]):
    id = "alertmanager"
    display_name = "Alertmanager"

    def load_settings(self, env_file: str | None) -> AlertmanagerSettings | None:
        settings = AlertmanagerSettings(_env_file=env_file)
        return settings if settings.enabled else None

    def build_client(self, settings: AlertmanagerSettings) -> AlertmanagerClient:
        assert settings.url is not None
        return AlertmanagerClient(
            settings.url,
            token=settings.token.get_secret_value() if settings.token else None,
            timeout_seconds=settings.request_timeout_seconds,
        )

    def tools(self) -> list[BaseTool]:
        return list(TOOLS)

    async def verify(self, client: AlertmanagerClient) -> VerifyResult:
        await client.alerts()
        return VerifyResult(self.id, VerifyStatus.OK, "Alertmanager alert read access confirmed")
