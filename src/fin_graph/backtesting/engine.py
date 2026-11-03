"""One-day immutable portfolio execution and valuation for the v1 backtester."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from fin_graph.adapters.reference_app.models import ReferenceHedgeFundResult
from fin_graph.domain.fills import apply_fill
from fin_graph.domain.orders import OrderAction
from fin_graph.domain.portfolio import Portfolio


@dataclass(frozen=True)
class DayExecution:
    portfolio: Portfolio
    portfolio_value: float
    executed_trades: dict[str, int]
    realized_pnl: dict[str, float]
    exit_pnl: tuple[float, ...]
    long_exposure: float
    short_exposure: float
    gross_exposure: float
    net_exposure: float
    long_short_ratio: float | None


def execute_day(
    portfolio: Portfolio,
    tickers: Sequence[str],
    prices: Mapping[str, float],
    result: ReferenceHedgeFundResult,
) -> DayExecution:
    """Execute decisions in request order, partially filling only what the book can support."""

    current = portfolio
    executed: dict[str, int] = {}
    realized: dict[str, float] = {}
    exits: list[float] = []
    for ticker in tickers:
        decision = result.decisions.get(ticker)
        if decision is None:
            executed[ticker] = 0
            realized[ticker] = 0.0
            continue
        fill = apply_fill(
            current,
            ticker,
            decision.action,
            decision.quantity,
            float(prices[ticker]),
            partial=True,
        )
        current = fill.portfolio
        executed[ticker] = fill.executed
        realized[ticker] = fill.realized_pnl
        if fill.executed and decision.action in (OrderAction.SELL, OrderAction.COVER):
            exits.append(fill.realized_pnl)

    long_exposure = sum(
        current.position(ticker).long * float(price) for ticker, price in prices.items()
    )
    short_exposure = sum(
        current.position(ticker).short * float(price) for ticker, price in prices.items()
    )
    return DayExecution(
        portfolio=current,
        # The compatibility engine intentionally mirrors ai-hedge-fund's historical
        # valuation: free cash + longs - shorts, without adding locked collateral.
        portfolio_value=current.cash + long_exposure - short_exposure,
        executed_trades=executed,
        realized_pnl=realized,
        exit_pnl=tuple(exits),
        long_exposure=long_exposure,
        short_exposure=short_exposure,
        gross_exposure=long_exposure + short_exposure,
        net_exposure=long_exposure - short_exposure,
        long_short_ratio=long_exposure / short_exposure if short_exposure else None,
    )
