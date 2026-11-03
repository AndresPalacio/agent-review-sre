"""Typed core models for the reference v1 hedge-fund result shape."""

from __future__ import annotations

from datetime import date
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fin_graph.domain.analysts import AnalystId, resolve_analysts
from fin_graph.domain.orders import OrderAction
from fin_graph.domain.portfolio import Portfolio
from fin_graph.domain.quant.common import Signal
from fin_graph.domain.requests.model import unique_tickers


class LegacyHedgeFundRequest(BaseModel):
    """The v1 application-service request before HTTP-specific flow adaptation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tickers: tuple[str, ...] = Field(min_length=1)
    start_date: date
    end_date: date
    portfolio: Portfolio = Field(default_factory=Portfolio)
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


class LegacyHedgeFundResult(BaseModel):
    """The exact top-level object returned by ``run_hedge_fund``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decisions: dict[str, LegacyPortfolioDecision]
    analyst_signals: dict[str, dict[str, LegacyAnalystSignal]]
