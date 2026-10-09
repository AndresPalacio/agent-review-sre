"""Grafana API settings."""

from __future__ import annotations

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class GrafanaSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GRAFANA_",
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
    def require_url(self) -> GrafanaSettings:
        if self.enabled and not self.url:
            raise ValueError("url is required when enabled")
        return self
