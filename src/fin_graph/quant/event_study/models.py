"""Typed event-study requests and results."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class EventStudyRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    tickers: tuple[str, ...]
    as_of: date
    price_start: date
    market_ticker: str = "SPY"
    earnings_limit: int = Field(default=12, ge=1)
    n_bootstrap: int = Field(default=10_000, ge=1)
    seed: int | None = None
    require_eps_surprise: bool = False


class MarketModelFit(BaseModel):
    model_config = ConfigDict(frozen=True)

    alpha: float
    beta: float
    r_squared: float
    n_obs: int


class EventCAR(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    event_date: date
    source_type: str
    report_period: date
    eps_surprise: str | None = None
    market_model: MarketModelFit
    daily_ar: tuple[float, ...] = ()
    car_0_1: float | None = None
    car_0_5: float | None = None
    car_0_20: float | None = None


class BootstrapCI(BaseModel):
    model_config = ConfigDict(frozen=True)

    lower: float
    upper: float
    confidence: float = 0.95
    n_bootstrap: int = 10_000


class WindowStats(BaseModel):
    model_config = ConfigDict(frozen=True)

    window: str
    n_events: int
    mean_car: float
    std_car: float
    t_stat: float
    p_value: float
    ci: BootstrapCI


class AggregateResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_type: str
    n_events: int
    windows: tuple[WindowStats, ...] = ()


class EventStudyResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    events: tuple[EventCAR, ...] = ()
    aggregates: tuple[AggregateResult, ...] = ()
    skipped_tickers: tuple[str, ...] = ()
    market_ticker: str
    as_of: date
    seed: int | None = None
