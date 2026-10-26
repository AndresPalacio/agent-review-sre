"""Runtime settings read from environment variables and an optional ``.env`` file."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class PersistenceBackend(StrEnum):
    """Where graph checkpoints and the long-term store live."""

    MEMORY = "memory"
    SQLITE = "sqlite"
    POSTGRES = "postgres"


class Settings(BaseSettings):
    """Runtime configuration; secrets are ``SecretStr`` and provider keys stay in their own vars.

    A variable set to an empty string counts as unset, so blank lines copied
    from ``.env.example`` fall back to the defaults below.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True
    )

    llm_provider: str = "anthropic"
    llm_model_reasoning: str | None = None
    llm_model_worker: str | None = None
    llm_model_fast: str | None = None
    llm_model_judge: str | None = None
    llm_fallback_models: Annotated[tuple[str, ...], NoDecode] = ()
    llm_temperature: float = 0.0
    llm_timeout_seconds: float = 120.0
    llm_max_retries: int = 2
    #: Context window requested from Ollama; its own default (2048) truncates analysis prompts.
    llm_num_ctx: int = Field(default=8192, ge=512)
    #: Output cap for Ollama: a small model in JSON mode can keep a list open until the context
    #: runs out; the cap turns that into a quick, recoverable failure.
    llm_num_predict: int = Field(default=1024, ge=64)
    ollama_base_url: str = "http://localhost:11434"
    persistence_backend: PersistenceBackend = PersistenceBackend.MEMORY
    embeddings_provider: str | None = None
    sqlite_path: str = "fin-graph.sqlite"
    database_url: SecretStr | None = None
    environment: str = "dev"
    auto_approve_tools: Annotated[tuple[str, ...], NoDecode] = ()
    approver_ids: Annotated[tuple[str, ...], NoDecode] = ()
    mask_enabled: bool = False
    mask_kinds: Annotated[tuple[str, ...], NoDecode] = ()
    mask_extra_regex: dict[str, str] = Field(default_factory=dict)
    mask_key: SecretStr | None = None
    # Finance: the paper portfolio, the data source and how analysts run.
    base_currency: str = "USD"
    initial_cash: float = Field(default=100_000.0, ge=0)
    margin_requirement: float = Field(default=0.5, ge=0, le=1)
    risk_free_rate: float = Field(default=0.045, ge=0, le=0.5)
    market_data_provider: Literal["yahoo", "financial_datasets", "fixtures"] = "yahoo"
    #: ``<dir>/<TICKER>/{prices,financial_metrics,line_items,insider_trades,news}.json`` offline.
    market_fixtures_dir: str | None = None
    fin_analyst_mode: Literal["auto", "react", "tool_plan"] = "auto"
    # Hard trading gates; the model never overrides them.
    trading_max_order_notional: float = Field(default=10_000.0, gt=0)
    trading_max_position_pct: float = Field(default=0.25, gt=0, le=1)
    trading_max_daily_orders: int = Field(default=10, ge=0)
    trading_short_selling_enabled: bool = False
    trading_allowed_actions: Annotated[tuple[str, ...], NoDecode] = ()
    trading_kill_switch: bool = False

    @field_validator("base_currency")
    @classmethod
    def _currency_code(cls, value: str) -> str:
        code = value.strip().upper()
        if len(code) != 3 or not code.isalpha():
            raise ValueError("must be a three-letter ISO 4217 code")
        return code

    @field_validator("llm_provider", "environment")
    @classmethod
    def _normalize_name(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("embeddings_provider")
    @classmethod
    def _normalize_embeddings_provider(cls, value: str | None) -> str | None:
        return value.strip().lower() if value else None

    @field_validator(
        "auto_approve_tools",
        "approver_ids",
        "mask_kinds",
        "llm_fallback_models",
        "trading_allowed_actions",
        mode="before",
    )
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    def require_database_url(self) -> str:
        """Return ``DATABASE_URL`` or fail naming the variable the operator must set."""
        if self.database_url is None or not self.database_url.get_secret_value():
            raise ValueError("DATABASE_URL must be set when PERSISTENCE_BACKEND=postgres")
        return self.database_url.get_secret_value()
