"""Settings for the keyless Yahoo Finance data provider."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class YahooSettings(BaseSettings):
    """On by default: Yahoo needs no key, and nothing is requested until a tool runs."""

    model_config = SettingsConfigDict(
        env_prefix="YAHOO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    enabled: bool = True
    request_timeout_seconds: float = Field(default=15.0, gt=0)
    #: Days after a fiscal year ends before its statements count as known (point in time).
    report_lag_days: int = Field(default=45, ge=0, le=365)
    #: The live TTM snapshot is used only when ``as_of`` is at most this many days old.
    snapshot_max_age_days: int = Field(default=7, ge=0, le=60)
