"""New Relic NerdGraph account, region and user key."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class NewRelicSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEWRELIC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    enabled: bool = False
    region: Literal["us", "eu", "jp"] = "us"
    api_key: SecretStr | None = None
    account_id: int | None = Field(default=None, gt=0)
    request_timeout_seconds: float = Field(default=15.0, gt=0)

    @model_validator(mode="after")
    def validate_enabled(self) -> NewRelicSettings:
        if self.enabled and not self.api_key:
            raise ValueError("api_key is required when enabled")
        if self.enabled and self.account_id is None:
            raise ValueError("account_id is required when enabled")
        return self

    @property
    def url(self) -> str:
        return {
            "us": "https://api.newrelic.com",
            "eu": "https://api.eu.newrelic.com",
            "jp": "https://api.jp.newrelic.com",
        }[self.region]
