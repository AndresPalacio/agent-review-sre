"""AWS region and optional local profile; credentials use the SDK chain."""

from __future__ import annotations

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AwsSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AWS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    enabled: bool = False
    region: str | None = None
    profile: str | None = None
    request_timeout_seconds: float = Field(default=15.0, gt=0)

    @model_validator(mode="after")
    def require_region(self) -> AwsSettings:
        if self.enabled and not self.region:
            raise ValueError("region is required when enabled")
        return self
