"""The paper portfolio the analysis sizes against: cash, long and short positions, margin.

Mirrors ai-hedge-fund's portfolio dict (``cash``, ``positions[ticker].long/short``,
``margin_requirement``, ``margin_used``) as typed models. Paper only: nothing here touches a broker.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field


class Position(BaseModel):
    """Shares held long and sold short in one ticker, with their average cost."""

    model_config = ConfigDict(frozen=True)

    long: int = Field(default=0, ge=0)
    short: int = Field(default=0, ge=0)
    long_cost_basis: float = Field(default=0.0, ge=0)
    short_cost_basis: float = Field(default=0.0, ge=0)

    @property
    def net_shares(self) -> int:
        return self.long - self.short

    def exposure(self, price: float) -> float:
        """Absolute net market value at ``price``."""
        return abs(self.net_shares * price)


class Portfolio(BaseModel):
    """Cash plus positions; ``margin_requirement`` is the fraction of a short held as collateral."""

    model_config = ConfigDict(frozen=True)

    cash: float = Field(default=100_000.0, ge=0)
    positions: dict[str, Position] = Field(default_factory=dict)
    margin_requirement: float = Field(default=0.5, ge=0, le=1)
    margin_used: float = Field(default=0.0, ge=0)
    currency: str = "USD"

    def position(self, ticker: str) -> Position:
        return self.positions.get(ticker, Position())

    def active_tickers(self) -> set[str]:
        """Tickers with a non-zero net position."""
        return {ticker for ticker, held in self.positions.items() if held.net_shares != 0}

    def net_liquidation_value(self, prices: Mapping[str, float]) -> float:
        """Cash, short collateral and longs, minus shorts, at ``prices``.

        ``cash`` is free cash: opening a short moves its margin into ``margin_used``
        (:func:`fin_graph.domain.fills.apply_fill`), and that collateral is still the
        portfolio's. Tickers without a price are left out.
        """
        total = self.cash + self.margin_used
        for ticker, held in self.positions.items():
            price = prices.get(ticker)
            if price is not None:
                total += held.long * price - held.short * price
        return total

    def equity(self, prices: Mapping[str, float]) -> float:
        """The equity margin is measured against (net liquidation value)."""
        return self.net_liquidation_value(prices)
