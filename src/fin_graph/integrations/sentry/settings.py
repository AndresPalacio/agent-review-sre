"""Sentry API settings."""

from __future__ import annotations

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SentrySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SENTRY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    enabled: bool = False
    url: str = "https://sentry.io"
    organization: str | None = None
    token: SecretStr | None = None
    request_timeout_seconds: float = Field(default=15.0, gt=0)

    @model_validator(mode="after")
    def validate_enabled(self) -> SentrySettings:
        if self.enabled and not self.organization:
            raise ValueError("organization is required when enabled")
        if self.enabled and not self.token:
            raise ValueError("token is required when enabled")
        return self
