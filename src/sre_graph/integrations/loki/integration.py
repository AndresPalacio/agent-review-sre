"""Loki registry adapter."""

from __future__ import annotations

import time

from langchain_core.tools import BaseTool

from sre_graph.integrations.base import Integration, VerifyResult, VerifyStatus
from sre_graph.integrations.loki.client import LokiClient
from sre_graph.integrations.loki.settings import LokiSettings
from sre_graph.integrations.loki.tools import TOOLS


class LokiIntegration(Integration[LokiSettings, LokiClient]):
    id = "loki"
    display_name = "Grafana Loki"

    def load_settings(self, env_file: str | None) -> LokiSettings | None:
        settings = LokiSettings(_env_file=env_file)
        return settings if settings.enabled else None

    def build_client(self, settings: LokiSettings) -> LokiClient:
        assert settings.url is not None
        return LokiClient(
            settings.url,
            token=settings.token.get_secret_value() if settings.token else None,
            tenant=settings.tenant,
            timeout_seconds=settings.request_timeout_seconds,
        )

    def tools(self) -> list[BaseTool]:
        return list(TOOLS)

    async def verify(self, client: LokiClient) -> VerifyResult:
        now = int(time.time())
        await client.labels(now - 300, now)
        return VerifyResult(self.id, VerifyStatus.OK, "Loki label read access confirmed")
