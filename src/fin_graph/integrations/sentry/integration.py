"""Sentry registry adapter."""

from __future__ import annotations

from langchain_core.tools import BaseTool

from fin_graph.integrations.base import Integration, VerifyResult, VerifyStatus
from fin_graph.integrations.sentry.client import SentryClient
from fin_graph.integrations.sentry.settings import SentrySettings
from fin_graph.integrations.sentry.tools import TOOLS


class SentryIntegration(Integration[SentrySettings, SentryClient]):
    id = "sentry"
    display_name = "Sentry"

    def load_settings(self, env_file: str | None) -> SentrySettings | None:
        settings = SentrySettings(_env_file=env_file)
        return settings if settings.enabled else None

    def build_client(self, settings: SentrySettings) -> SentryClient:
        assert settings.organization and settings.token
        return SentryClient(
            settings.url,
            settings.organization,
            settings.token.get_secret_value(),
            timeout_seconds=settings.request_timeout_seconds,
        )

    def tools(self) -> list[BaseTool]:
        return list(TOOLS)

    async def verify(self, client: SentryClient) -> VerifyResult:
        await client.organization_read()
        return VerifyResult(self.id, VerifyStatus.OK, "Sentry organization read access confirmed")
