"""``K8S_*`` settings. The integration stays off until ``K8S_ENABLED=true``."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

INTEGRATION_ID = "kubernetes"


class KubernetesSettings(BaseSettings):
    """Where the cluster credentials come from and how far each call may go."""

    model_config = SettingsConfigDict(
        env_prefix="K8S_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    enabled: bool = False
    #: Path to a kubeconfig file; unset means ``KUBECONFIG`` or ``~/.kube/config``.
    kubeconfig: str | None = None
    #: Kubeconfig context to use; unset means the file's current context.
    context: str | None = None
    #: Use the service account of the pod this process runs in instead of a kubeconfig.
    in_cluster: bool = False
    #: Namespace the connectivity check lists pods in.
    verify_namespace: str = "default"
    request_timeout_seconds: float = Field(default=30.0, gt=0)
    #: Most items any single list call asks the API server for.
    max_items: int = Field(default=200, ge=1, le=1000)
