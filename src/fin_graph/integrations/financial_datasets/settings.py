"""Settings for the Financial Datasets API."""

from __future__ import annotations

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class FinancialDatasetsSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FINANCIAL_DATASETS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    enabled: bool = False
    api_key: SecretStr | None = None
    url: str = "https://api.financialdatasets.ai"
    request_timeout_seconds: float = Field(default=15.0, gt=0)

    @model_validator(mode="after")
    def require_key(self) -> FinancialDatasetsSettings:
        if self.enabled and (self.api_key is None or not self.api_key.get_secret_value()):
            raise ValueError("api_key is required when enabled")
        return self
