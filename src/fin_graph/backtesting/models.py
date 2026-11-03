"""Typed, transport-neutral models for the v1 point-in-time backtester."""

from __future__ import annotations

from datetime import date as Date
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fin_graph.compat.ai_hedge_fund.models import (
    LegacyAnalystSignal,
    LegacyPortfolioDecision,
    LegacyRiskAnalysis,
)
from fin_graph.domain.analysts import AnalystId, resolve_analysts
from fin_graph.domain.portfolio import Portfolio
from fin_graph.domain.requests.model import unique_tickers


class BacktestStatus(StrEnum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class BacktestEventKind(StrEnum):
    RUNNING = "running"
    PROGRESS = "progress"
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    ERROR = "error"


class BacktestRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tickers: tuple[str, ...] = Field(min_length=1)
    start_date: Date
    end_date: Date
    initial_capital: float = Field(default=100_000.0, gt=0, allow_inf_nan=False)
    margin_requirement: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    analysts: tuple[AnalystId, ...] | None = None
    model: str = Field(default="gpt-4.1", min_length=1)

    @field_validator("tickers", mode="before")
    @classmethod
    def normalize_tickers(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.split(",")
        if isinstance(value, list | tuple):
            return tuple(unique_tickers(str(item) for item in value))
        return value

    @field_validator("analysts", mode="before")
    @classmethod
    def validate_analysts(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            value = tuple(item.strip() for item in value.split(",") if item.strip())
        if isinstance(value, list | tuple):
            return tuple(definition.id for definition in resolve_analysts(value)) if value else None
        return value

    @model_validator(mode="after")
    def validate_dates(self) -> Self:
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        return self


class BacktestSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    date: Date
    portfolio: Portfolio
    portfolio_value: float = Field(allow_inf_nan=False)
    decisions: dict[str, LegacyPortfolioDecision]
    executed_trades: dict[str, int]
    realized_pnl: dict[str, float]
    analyst_signals: dict[str, dict[str, LegacyAnalystSignal | LegacyRiskAnalysis]]
    current_prices: dict[str, float]
    benchmark_return_pct: float | None = None
    long_exposure: float = 0
    short_exposure: float = 0
    gross_exposure: float = 0
    net_exposure: float = 0
    long_short_ratio: float | None = None


class BacktestMetrics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    total_return_pct: float = 0
    benchmark_return_pct: float | None = None
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    max_drawdown_pct: float = 0
    max_drawdown_date: Date | None = None
    win_rate_pct: float | None = None
    long_short_ratio: float | None = None
    gross_exposure: float = 0
    net_exposure: float = 0


class BacktestResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request: BacktestRequest
    snapshots: tuple[BacktestSnapshot, ...]
    metrics: BacktestMetrics
    final_portfolio: Portfolio
    status: BacktestStatus


class BacktestEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sequence: int = Field(ge=0)
    kind: BacktestEventKind
    progress: float = Field(ge=0, le=1)
    message: str
    date: Date | None = None

    @property
    def terminal(self) -> bool:
        return self.kind in {
            BacktestEventKind.COMPLETE,
            BacktestEventKind.CANCELLED,
            BacktestEventKind.ERROR,
        }
