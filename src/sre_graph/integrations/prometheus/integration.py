"""Registry adapters for Prometheus and Mimir."""

from __future__ import annotations

from langchain_core.tools import BaseTool

from sre_graph.integrations.base import Integration, VerifyResult, VerifyStatus
from sre_graph.integrations.prometheus.client import PrometheusClient
from sre_graph.integrations.prometheus.settings import MimirSettings, PrometheusSettings
from sre_graph.integrations.prometheus.tools import MIMIR_TOOLS, PROM_TOOLS


class PrometheusIntegration(Integration[PrometheusSettings, PrometheusClient]):
    id = "prometheus"
    display_name = "Prometheus"

    def load_settings(self, env_file: str | None) -> PrometheusSettings | None:
        settings = PrometheusSettings(_env_file=env_file)
        return settings if settings.enabled else None

    def build_client(self, settings: PrometheusSettings) -> PrometheusClient:
        assert settings.url is not None
        return PrometheusClient(
            settings.url,
            token=settings.token.get_secret_value() if settings.token else None,
            timeout_seconds=settings.request_timeout_seconds,
        )

    def tools(self) -> list[BaseTool]:
        return list(PROM_TOOLS)

    async def verify(self, client: PrometheusClient) -> VerifyResult:
        await client.query("up")
        return VerifyResult(self.id, VerifyStatus.OK, "Prometheus query access confirmed")


class MimirIntegration(Integration[MimirSettings, PrometheusClient]):
    id = "mimir"
    display_name = "Grafana Mimir"

    def load_settings(self, env_file: str | None) -> MimirSettings | None:
        settings = MimirSettings(_env_file=env_file)
        return settings if settings.enabled else None

    def build_client(self, settings: MimirSettings) -> PrometheusClient:
        assert settings.url is not None
        return PrometheusClient(
            settings.url,
            token=settings.token.get_secret_value() if settings.token else None,
            tenant=settings.tenant,
            timeout_seconds=settings.request_timeout_seconds,
            vendor="Mimir",
        )

    def tools(self) -> list[BaseTool]:
        return list(MIMIR_TOOLS)

    async def verify(self, client: PrometheusClient) -> VerifyResult:
        await client.query("up")
        return VerifyResult(self.id, VerifyStatus.OK, "Mimir query access confirmed")
