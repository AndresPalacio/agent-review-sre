"""Server-only configuration; secret values are never serialized in responses."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from sre_graph.delivery.settings import DeliveryDestinations


class ServerConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True
    )

    database_url: SecretStr | None = None
    bootstrap_admin_key: SecretStr | None = None
    max_concurrent_runs: int = Field(default=4, ge=1, le=64)
    max_pending_runs: int = Field(default=128, ge=1, le=4096)
    max_http_requests: int = Field(default=128, ge=1, le=4096)
    max_streams: int = Field(default=64, ge=1, le=1024)
    scheduler_election_seconds: float = Field(default=3, ge=0.05, le=60)
    stream_fallback_seconds: float = Field(default=15, ge=0.05, le=60)
    thread_retention_days: int = Field(default=30, ge=1)
    approval_ttl_hours: int = Field(default=24, ge=1, le=720)
    alert_dedupe_minutes: int = Field(default=30, ge=1)
    alert_delivery_routes: dict[
        Literal["default", "critical", "high", "warning", "info"], DeliveryDestinations
    ] = Field(default_factory=dict)
    cors_origins: Annotated[tuple[str, ...], NoDecode] = ()
    webhook_tokens: dict[str, SecretStr] = Field(default_factory=dict)
    webhook_hmac_secrets: dict[str, SecretStr] = Field(default_factory=dict)
    sns_topic_arn: str | None = None
    sns_cert_url: str | None = None
    sns_public_key_path: str | None = None

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    def require_database_url(self) -> str:
        if self.database_url is None or not self.database_url.get_secret_value():
            raise ValueError("DATABASE_URL is required for the API server")
        url = self.database_url.get_secret_value()
        if not url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError("API server requires Postgres")
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
