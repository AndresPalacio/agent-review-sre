"""Settings for Prometheus and Grafana Mimir query endpoints."""

from __future__ import annotations

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PrometheusSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PROMETHEUS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    enabled: bool = False
    url: str | None = None
    token: SecretStr | None = None
    request_timeout_seconds: float = Field(default=15.0, gt=0)

    @model_validator(mode="after")
    def require_url(self) -> PrometheusSettings:
        if self.enabled and not self.url:
            raise ValueError("url is required when enabled")
        return self


class MimirSettings(PrometheusSettings):
    model_config = SettingsConfigDict(
        env_prefix="MIMIR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    tenant: str | None = None
