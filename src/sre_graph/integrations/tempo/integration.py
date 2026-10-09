"""Tempo registry adapter."""

from __future__ import annotations

import time

from langchain_core.tools import BaseTool

from sre_graph.integrations.base import Integration, VerifyResult, VerifyStatus
from sre_graph.integrations.tempo.client import TempoClient
from sre_graph.integrations.tempo.settings import TempoSettings
from sre_graph.integrations.tempo.tools import TOOLS


class TempoIntegration(Integration[TempoSettings, TempoClient]):
    id = "tempo"
    display_name = "Grafana Tempo"

    def load_settings(self, env_file: str | None) -> TempoSettings | None:
        settings = TempoSettings(_env_file=env_file)
        return settings if settings.enabled else None

    def build_client(self, settings: TempoSettings) -> TempoClient:
        assert settings.url is not None
        return TempoClient(
            settings.url,
            token=settings.token.get_secret_value() if settings.token else None,
            tenant=settings.tenant,
            timeout_seconds=settings.request_timeout_seconds,
        )

    def tools(self) -> list[BaseTool]:
        return list(TOOLS)

    async def verify(self, client: TempoClient) -> VerifyResult:
        now = int(time.time())
        await client.search("{}", now - 300, now, 1)
        return VerifyResult(self.id, VerifyStatus.OK, "Tempo trace search access confirmed")
