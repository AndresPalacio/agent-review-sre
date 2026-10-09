"""The Kubernetes integration: kubeconfig or in-cluster credentials, read-only tools."""

from __future__ import annotations

from langchain_core.tools import BaseTool

from sre_graph.integrations.base import Integration, VerifyResult, VerifyStatus
from sre_graph.integrations.kubernetes.client import KubernetesClient
from sre_graph.integrations.kubernetes.settings import INTEGRATION_ID, KubernetesSettings
from sre_graph.integrations.kubernetes.tools import TOOLS


class KubernetesIntegration(Integration[KubernetesSettings, KubernetesClient]):
    """Pods, logs, events, workloads, rollouts, nodes, autoscalers and usage of one cluster."""

    id = INTEGRATION_ID
    display_name = "Kubernetes"

    def load_settings(self, env_file: str | None) -> KubernetesSettings | None:
        settings = KubernetesSettings(_env_file=env_file)
        return settings if settings.enabled else None

    def build_client(self, settings: KubernetesSettings) -> KubernetesClient:
        return KubernetesClient.from_settings(settings)

    def tools(self) -> list[BaseTool]:
        return list(TOOLS)

    async def verify(self, client: KubernetesClient) -> VerifyResult:
        """Reach the API server, then prove the credentials can list pods.

        ``/version`` alone proves nothing about credentials: many clusters
        serve it to anonymous users.
        """
        version = await client.version()
        await client.list_pods(client.verify_namespace, limit=1)
        return VerifyResult(
            self.id,
            VerifyStatus.OK,
            f"Kubernetes {version} at {client.host}; can list pods in {client.verify_namespace}",
        )
