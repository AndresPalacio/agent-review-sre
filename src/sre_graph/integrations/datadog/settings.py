"""Datadog site and API key settings."""

from __future__ import annotations

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SITES = frozenset(
    {
        "datadoghq.com",
        "us3.datadoghq.com",
        "us5.datadoghq.com",
        "datadoghq.eu",
        "ap1.datadoghq.com",
        "ap2.datadoghq.com",
        "uk1.datadoghq.com",
        "ddog-gov.com",
        "us2.ddog-gov.com",
    }
)


class DatadogSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DATADOG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    enabled: bool = False
    site: str = "datadoghq.com"
    api_key: SecretStr | None = None
    app_key: SecretStr | None = None
    request_timeout_seconds: float = Field(default=15.0, gt=0)

    @model_validator(mode="after")
    def validate_enabled(self) -> DatadogSettings:
        if self.site not in _SITES:
            raise ValueError("site is not a supported Datadog site")
        if self.enabled and not self.api_key:
            raise ValueError("api_key is required when enabled")
        if self.enabled and not self.app_key:
            raise ValueError("app_key is required when enabled")
        return self

    @property
    def url(self) -> str:
        return f"https://api.{self.site}"
