"""Typed core models for the reference v1 hedge-fund result shape."""

from __future__ import annotations

from datetime import date
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fin_graph.domain.analysts import AnalystId, resolve_analysts
from fin_graph.domain.orders import OrderAction
from fin_graph.domain.portfolio import Portfolio, Position, RealizedGains
from fin_graph.domain.quant.common import Signal
from fin_graph.domain.requests.model import unique_tickers


class LegacyPosition(BaseModel):
    """Lossless v1 position record, including per-ticker short collateral."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    long: int = Field(default=0, ge=0)
    short: int = Field(default=0, ge=0)
    long_cost_basis: float = Field(default=0.0, ge=0)
    short_cost_basis: float = Field(default=0.0, ge=0)
    short_margin_used: float = Field(default=0.0, ge=0)


class LegacyRealizedGains(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    long: float = 0.0
    short: float = 0.0


class LegacyPortfolio(BaseModel):
    """Exact v1 portfolio dictionary with a typed adapter to the native ledger."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cash: float = Field(default=100_000.0, ge=0)
    margin_used: float = Field(default=0.0, ge=0)
    margin_requirement: float = Field(default=0.5, ge=0, le=1)
    positions: dict[str, LegacyPosition] = Field(default_factory=dict)
    realized_gains: dict[str, LegacyRealizedGains] = Field(default_factory=dict)

    def to_native(self) -> Portfolio:
        return Portfolio(
            cash=self.cash,
            margin_used=self.margin_used,
            margin_requirement=self.margin_requirement,
            positions={
                ticker: Position(
                    long=position.long,
                    short=position.short,
                    long_cost_basis=position.long_cost_basis,
                    short_cost_basis=position.short_cost_basis,
                    short_margin_used=position.short_margin_used,
                )
                for ticker, position in self.positions.items()
            },
            realized_gains={
                ticker: RealizedGains(long=gains.long, short=gains.short)
                for ticker, gains in self.realized_gains.items()
            },
        )

    @classmethod
    def from_native(cls, portfolio: Portfolio) -> LegacyPortfolio:
        return cls(
            cash=portfolio.cash,
            margin_used=portfolio.margin_used,
            margin_requirement=portfolio.margin_requirement,
            positions={
                ticker: LegacyPosition(
                    long=position.long,
                    short=position.short,
                    long_cost_basis=position.long_cost_basis,
                    short_cost_basis=position.short_cost_basis,
                    short_margin_used=position.short_margin_used,
                )
                for ticker, position in portfolio.positions.items()
            },
            realized_gains={
                ticker: LegacyRealizedGains(long=gains.long, short=gains.short)
                for ticker, gains in portfolio.realized_gains.items()
            },
        )


class LegacyHedgeFundRequest(BaseModel):
    """The v1 application-service request before HTTP-specific flow adaptation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tickers: tuple[str, ...] = Field(min_length=1)
    start_date: date
    end_date: date
    portfolio: LegacyPortfolio = Field(default_factory=LegacyPortfolio)
    show_reasoning: bool = False
    selected_analysts: tuple[AnalystId, ...] | None = None
    model_name: str = "gpt-4.1"
    model_provider: str = "OpenAI"

    @field_validator("tickers", mode="before")
    @classmethod
    def normalize_tickers(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.split(",")
        if isinstance(value, list | tuple):
            return tuple(unique_tickers(str(item) for item in value))
        return value

    @field_validator("selected_analysts", mode="before")
    @classmethod
    def validate_analysts(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            value = tuple(item.strip() for item in value.split(",") if item.strip())
        if isinstance(value, list | tuple):
            return tuple(definition.id for definition in resolve_analysts(value)) if value else None
        return value

    @field_validator("portfolio", mode="before")
    @classmethod
    def adapt_native_portfolio(cls, value: object) -> object:
        if isinstance(value, Portfolio):
            return LegacyPortfolio.from_native(value)
        return value

    @model_validator(mode="after")
    def dates_are_ordered(self) -> Self:
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        return self


class LegacyAnalystSignal(BaseModel):
    """The public ``signal/confidence/reasoning`` object emitted by a v1 analyst."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    signal: Signal
    confidence: float = Field(ge=0, le=100)
    reasoning: dict[str, Any] | str | None = None


class LegacyPortfolioDecision(BaseModel):
    """A v1 portfolio-manager decision with its recorded field names."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: OrderAction
    quantity: int = Field(ge=0)
    confidence: int = Field(ge=0, le=100)
    reasoning: str


class LegacyVolatilityMetrics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    daily_volatility: float
    annualized_volatility: float
    volatility_percentile: float
    data_points: int = Field(ge=0)


class LegacyCorrelatedTicker(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: str
    correlation: float


class LegacyCorrelationMetrics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    avg_correlation_with_active: float | None = None
    max_correlation_with_active: float | None = None
    top_correlated_tickers: tuple[LegacyCorrelatedTicker, ...] = ()


class LegacyRiskAnalysis(BaseModel):
    """Per-ticker payload produced by the v1 ``risk_management_agent``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    remaining_position_limit: float
    current_price: float
    volatility_metrics: LegacyVolatilityMetrics | None = None
    correlation_metrics: LegacyCorrelationMetrics | None = None
    reasoning: dict[str, Any]


class LegacyHedgeFundResult(BaseModel):
    """The exact top-level object returned by ``run_hedge_fund``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decisions: dict[str, LegacyPortfolioDecision]
    analyst_signals: dict[str, dict[str, LegacyAnalystSignal | LegacyRiskAnalysis]]
