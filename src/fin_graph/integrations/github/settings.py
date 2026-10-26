"""GitHub repository read settings."""

from __future__ import annotations

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class GitHubSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GITHUB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    enabled: bool = False
    url: str = "https://api.github.com"
    owner: str | None = None
    repo: str | None = None
    token: SecretStr | None = None
    request_timeout_seconds: float = Field(default=15.0, gt=0)

    @model_validator(mode="after")
    def validate_enabled(self) -> GitHubSettings:
        for name in ("owner", "repo", "token"):
            if self.enabled and not getattr(self, name):
                raise ValueError(f"{name} is required when enabled")
        return self
