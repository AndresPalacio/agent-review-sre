"""Separate, disabled-by-default inbound bot credentials and allowlists."""

from __future__ import annotations

from typing import Annotated

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _ids(value: object) -> object:
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    return value


class SlackGatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SLACK_GATEWAY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    enabled: bool = False
    bot_token: SecretStr | None = None
    signing_secret: SecretStr | None = None
    app_token: SecretStr | None = None
    socket_mode: bool = False
    channel_id: str | None = None
    approver_user_ids: Annotated[tuple[str, ...], NoDecode] = ()
    max_body_bytes: int = 1024 * 1024

    @field_validator("approver_user_ids", mode="before")
    @classmethod
    def parse_ids(cls, value: object) -> object:
        return _ids(value)

    @model_validator(mode="after")
    def validate_enabled(self) -> SlackGatewaySettings:
        if self.enabled and not (self.bot_token and self.signing_secret and self.channel_id):
            raise ValueError("bot_token, signing_secret and channel_id are required when enabled")
        if self.socket_mode and (not self.enabled or not self.app_token):
            raise ValueError("enabled gateway and app_token are required for socket mode")
        if self.max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be positive")
        return self


class TelegramGatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TELEGRAM_GATEWAY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    enabled: bool = False
    bot_token: SecretStr | None = None
    webhook_secret: SecretStr | None = None
    chat_id: str | None = None
    approver_user_ids: Annotated[tuple[str, ...], NoDecode] = ()
    max_body_bytes: int = 1024 * 1024

    @field_validator("approver_user_ids", mode="before")
    @classmethod
    def parse_ids(cls, value: object) -> object:
        return _ids(value)

    @model_validator(mode="after")
    def validate_enabled(self) -> TelegramGatewaySettings:
        if self.enabled and not (self.bot_token and self.webhook_secret and self.chat_id):
            raise ValueError("bot_token, webhook_secret and chat_id are required when enabled")
        if self.max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be positive")
        return self
