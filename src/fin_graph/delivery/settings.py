"""Independent Slack and Telegram delivery settings."""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def validate_destinations(value: dict[str, str]) -> dict[str, str]:
    if any(
        channel not in {"slack", "telegram"} or not destination.strip() or len(destination) > 128
        for channel, destination in value.items()
    ):
        raise ValueError("invalid report destinations")
    return {channel: destination.strip() for channel, destination in value.items()}


DeliveryDestinations = Annotated[dict[str, str], AfterValidator(validate_destinations)]


class SlackSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SLACK_DELIVERY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    enabled: bool = False
    token: SecretStr | None = None
    request_timeout_seconds: float = Field(default=15.0, gt=0)

    @model_validator(mode="after")
    def validate_enabled(self) -> SlackSettings:
        if self.enabled and not self.token:
            raise ValueError("token is required when enabled")
        return self


class TelegramSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TELEGRAM_DELIVERY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    enabled: bool = False
    token: SecretStr | None = None
    request_timeout_seconds: float = Field(default=15.0, gt=0)

    @model_validator(mode="after")
    def validate_enabled(self) -> TelegramSettings:
        if self.enabled and not self.token:
            raise ValueError("token is required when enabled")
        return self
