"""Immutable, point-in-time company input shared by deterministic persona scorers."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fin_graph.domain.market import CompanyNews, FinancialMetrics, InsiderTrade, LineItem, Price


class CompanySnapshot(BaseModel):
    """All observations known for one ticker at ``as_of``; future data is invalid."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: str = Field(min_length=1)
    as_of: date
    prices: tuple[Price, ...] = ()
    metrics: tuple[FinancialMetrics, ...] = ()
    line_items: tuple[LineItem, ...] = ()
    news: tuple[CompanyNews, ...] = ()
    insider_trades: tuple[InsiderTrade, ...] = ()
    market_cap: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        ticker = value.strip().upper()
        if not ticker:
            raise ValueError("ticker must not be blank")
        return ticker

    @model_validator(mode="after")
    def enforce_point_in_time(self) -> CompanySnapshot:
        dated = (
            ("prices", tuple(item.day for item in self.prices)),
            ("metrics", tuple(item.available_on for item in self.metrics)),
            ("line_items", tuple(item.available_on for item in self.line_items)),
            ("news", tuple(item.date.date() for item in self.news)),
            ("insider_trades", tuple(item.filing_date for item in self.insider_trades)),
        )
        for field, observations in dated:
            if any(observed > self.as_of for observed in observations):
                raise ValueError(f"future {field} observation after as_of")
        ticker_fields = (
            ("metrics", tuple(item.ticker for item in self.metrics)),
            ("line_items", tuple(item.ticker for item in self.line_items)),
            ("news", tuple(item.ticker for item in self.news)),
            ("insider_trades", tuple(item.ticker for item in self.insider_trades)),
        )
        for field, tickers in ticker_fields:
            if any(ticker.strip().upper() != self.ticker for ticker in tickers):
                raise ValueError(f"{field} observation belongs to another ticker")
        # Persona scorers use index zero for the latest filed fundamentals, while price
        # calculations expect chronological bars. Canonicalize once at the domain boundary so
        # provider-specific ordering cannot silently change a score.
        object.__setattr__(self, "prices", tuple(sorted(self.prices, key=lambda item: item.time)))
        object.__setattr__(
            self,
            "metrics",
            tuple(sorted(self.metrics, key=lambda item: item.report_period, reverse=True)),
        )
        object.__setattr__(
            self,
            "line_items",
            tuple(sorted(self.line_items, key=lambda item: item.report_period, reverse=True)),
        )
        object.__setattr__(
            self,
            "news",
            tuple(sorted(self.news, key=lambda item: item.date, reverse=True)),
        )
        object.__setattr__(
            self,
            "insider_trades",
            tuple(sorted(self.insider_trades, key=lambda item: item.filing_date, reverse=True)),
        )
        return self
