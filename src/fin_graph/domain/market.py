"""Market data as every provider must hand it over: prices, metrics, line items, news, insiders.

Shapes follow ai-hedge-fund's ``src/data/models.py`` (MIT) so its fixtures load unchanged,
with dates typed and every metric optional: a provider that lacks a number leaves it ``None``
and the quant functions treat it as a data gap, never as zero.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

type NewsSentiment = Literal["positive", "negative", "neutral"]


def _utc(value: object) -> object:
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    if isinstance(value, str) and value.endswith("Z"):
        return value[:-1] + "+00:00"
    return value


class Price(BaseModel):
    """One daily bar. ``time`` is the bar's timestamp in UTC."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    time: datetime
    open: float
    high: float
    low: float
    close: float
    adj_close: float | None = None
    volume: int

    @field_validator("time", mode="before")
    @classmethod
    def _time_utc(cls, value: object) -> object:
        return _utc(value)

    @field_validator("volume", mode="before")
    @classmethod
    def _whole_volume(cls, value: object) -> object:
        return int(value) if isinstance(value, float) else value

    @property
    def day(self) -> date:
        """The trading day the bar belongs to."""
        return self.time.astimezone(UTC).date()

    @property
    def effective_close(self) -> float:
        """Split/dividend-adjusted close when available, otherwise the raw close."""

        return self.adj_close if self.adj_close is not None else self.close


class FinancialMetrics(BaseModel):
    """Ratios for one reporting period; ``period`` is ``ttm``, ``annual`` or ``quarterly``."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    ticker: str
    report_period: date
    filing_date: date | None = None
    period: str = "ttm"
    currency: str = "USD"
    market_cap: float | None = None
    enterprise_value: float | None = None
    price_to_earnings_ratio: float | None = None
    price_to_book_ratio: float | None = None
    price_to_sales_ratio: float | None = None
    enterprise_value_to_ebitda_ratio: float | None = None
    enterprise_value_to_revenue_ratio: float | None = None
    free_cash_flow_yield: float | None = None
    peg_ratio: float | None = None
    gross_margin: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None
    return_on_equity: float | None = None
    return_on_assets: float | None = None
    return_on_invested_capital: float | None = None
    asset_turnover: float | None = None
    inventory_turnover: float | None = None
    receivables_turnover: float | None = None
    days_sales_outstanding: float | None = None
    operating_cycle: float | None = None
    working_capital_turnover: float | None = None
    current_ratio: float | None = None
    quick_ratio: float | None = None
    cash_ratio: float | None = None
    operating_cash_flow_ratio: float | None = None
    debt_to_equity: float | None = None
    debt_to_assets: float | None = None
    interest_coverage: float | None = None
    revenue_growth: float | None = None
    earnings_growth: float | None = None
    book_value_growth: float | None = None
    earnings_per_share_growth: float | None = None
    free_cash_flow_growth: float | None = None
    operating_income_growth: float | None = None
    ebitda_growth: float | None = None
    payout_ratio: float | None = None
    earnings_per_share: float | None = None
    book_value_per_share: float | None = None
    free_cash_flow_per_share: float | None = None

    @property
    def available_on(self) -> date:
        """First day this period was knowable; old providers fall back to period end."""

        return self.filing_date or self.report_period


class LineItem(BaseModel):
    """Statement lines for one period; the line names vary, so they are extra fields.

    Cash outflows (capital expenditure, dividends, buybacks) keep the provider's negative sign.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    ticker: str
    report_period: date
    filing_date: date | None = None
    period: str = "ttm"
    currency: str = "USD"

    @property
    def available_on(self) -> date:
        """First day this statement was knowable; old fixtures use period end."""

        return self.filing_date or self.report_period

    def value(self, name: str) -> float | None:
        """The numeric line ``name``, or ``None`` when absent or not a number."""
        raw = (self.model_extra or {}).get(name)
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            return None
        return float(raw)


class CompanyNews(BaseModel):
    """A headline; ``sentiment`` is the provider's label when it has one."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    ticker: str
    title: str
    source: str = ""
    date: datetime
    url: str = ""
    author: str | None = None
    sentiment: NewsSentiment | None = None

    @field_validator("date", mode="before")
    @classmethod
    def _date_utc(cls, value: object) -> object:
        return _utc(value)


class InsiderTrade(BaseModel):
    """One insider filing; negative ``transaction_shares`` is a sale."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    ticker: str
    filing_date: date
    issuer: str | None = None
    name: str | None = None
    title: str | None = None
    is_board_director: bool | None = None
    transaction_date: date | None = None
    transaction_shares: float | None = None
    transaction_price_per_share: float | None = None
    transaction_value: float | None = None
    shares_owned_before_transaction: float | None = None
    shares_owned_after_transaction: float | None = None
    security_title: str | None = None

    @field_validator("filing_date", "transaction_date", mode="before")
    @classmethod
    def _date_only(cls, value: object) -> object:
        if isinstance(value, str) and "T" in value:
            return value.split("T", 1)[0]
        return value


def sorted_prices(prices: Iterable[Price]) -> list[Price]:
    """Bars oldest first, one per timestamp (the last one seen wins)."""
    unique = {bar.time: bar for bar in prices}
    return [unique[moment] for moment in sorted(unique)]


def prices_until(prices: Iterable[Price], as_of: date) -> list[Price]:
    """Point-in-time view: only bars on or before ``as_of``, oldest first."""
    return [bar for bar in sorted_prices(prices) if bar.day <= as_of]


def closes(prices: Sequence[Price]) -> list[float]:
    """Adjusted closing prices in order, with raw-close fallback."""
    return [bar.effective_close for bar in prices]


def latest_first[T: FinancialMetrics | LineItem](periods: Iterable[T], as_of: date) -> list[T]:
    """Periods reported on or before ``as_of``, newest first: the order every scorer expects."""
    known = [item for item in periods if item.available_on <= as_of]
    return sorted(known, key=lambda item: item.report_period, reverse=True)
