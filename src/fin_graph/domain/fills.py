"""Apply an executed trade to a paper portfolio: cash, positions, cost basis, margin, P&L.

Ported from ai-hedge-fund ``src/backtesting/portfolio.py`` (MIT), with three deliberate changes:

- Short collateral is counted in the net liquidation value (``Portfolio.net_liquidation_value``
  adds ``margin_used``). The original moved it out of cash and never added it back, so opening a
  short made the portfolio look poorer by the margin.
- A short needs its margin from free cash. The original subtracted ``margin_used`` a second time.
- ``partial=False`` refuses an order the portfolio cannot fully afford instead of filling less:
  a human approved that quantity. The backtester passes ``partial=True`` for the original
  "fill what fits" behaviour.

Collateral is ``quantity x price x margin_requirement`` per short. Covering releases it in
proportion at the average short price, which equals the original's per-position tracking while
the margin requirement does not change.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from fin_graph.domain.orders import OrderAction, ProposedOrder
from fin_graph.domain.portfolio import Portfolio, Position


class InsufficientFunds(ValueError):
    """The portfolio cannot pay for (or collateralize) the whole order."""


class OrderStatus(StrEnum):
    """A paper order's life: queued outside market hours, then filled, cancelled or rejected."""

    QUEUED = "queued"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class PaperExecution(BaseModel):
    """What the paper broker did with an approved order; ``client_order_id`` is its key."""

    model_config = ConfigDict(frozen=True)

    order: ProposedOrder
    status: OrderStatus
    executed: int = Field(default=0, ge=0)
    fill_price: float | None = Field(default=None, gt=0)
    realized_pnl: float = 0.0
    reason: str = ""
    submitted_at: datetime
    updated_at: datetime

    @property
    def client_order_id(self) -> str:
        return self.order.client_order_id

    @property
    def done(self) -> bool:
        """Filled, cancelled and rejected orders never change again."""
        return self.status is not OrderStatus.QUEUED


class FillResult(BaseModel):
    """The portfolio after a fill, what was executed and the profit it realized."""

    model_config = ConfigDict(frozen=True)

    portfolio: Portfolio
    action: OrderAction
    requested: int = Field(ge=0)
    executed: int = Field(ge=0)
    price: float = Field(ge=0)
    realized_pnl: float = 0.0

    @property
    def notional(self) -> float:
        return self.executed * self.price


def _weighted(old_shares: int, old_basis: float, shares: int, price: float) -> float:
    total = old_shares + shares
    return (old_shares * old_basis + shares * price) / total if total else 0.0


def apply_fill(
    portfolio: Portfolio,
    ticker: str,
    action: OrderAction,
    quantity: int,
    price: float,
    *,
    partial: bool = False,
) -> FillResult:
    """``portfolio`` after trading ``quantity`` shares of ``ticker`` at ``price``.

    Sell and cover never exceed the shares held (with ``partial=False`` asking for more is an
    error). Hold, a zero quantity or a non-positive price change nothing.
    """
    held = portfolio.position(ticker)
    unchanged = FillResult(
        portfolio=portfolio, action=action, requested=max(quantity, 0), executed=0, price=price
    )
    if action is OrderAction.HOLD or quantity <= 0 or price <= 0:
        return unchanged
    cash, margin_used, realized = portfolio.cash, portfolio.margin_used, 0.0
    ratio = portfolio.margin_requirement
    if action is OrderAction.BUY:
        affordable = int(cash // price)
        executed = min(quantity, affordable)
        if executed < quantity and not partial:
            raise InsufficientFunds(f"buy needs {quantity * price:,.2f}, cash is {cash:,.2f}")
        position = held.model_copy(
            update={
                "long": held.long + executed,
                "long_cost_basis": _weighted(held.long, held.long_cost_basis, executed, price),
            }
        )
        cash -= executed * price
    elif action is OrderAction.SELL:
        executed = min(quantity, held.long)
        if executed < quantity and not partial:
            raise InsufficientFunds(f"sells {quantity}, holds {held.long}")
        realized = (price - held.long_cost_basis) * executed
        remaining = held.long - executed
        position = held.model_copy(
            update={
                "long": remaining,
                "long_cost_basis": held.long_cost_basis if remaining else 0.0,
            }
        )
        cash += executed * price
    elif action is OrderAction.SHORT:
        affordable = int(cash // (price * ratio)) if ratio > 0 else quantity
        executed = min(quantity, affordable)
        if executed < quantity and not partial:
            needed = quantity * price * ratio
            raise InsufficientFunds(f"short needs {needed:,.2f} of margin, cash is {cash:,.2f}")
        margin = executed * price * ratio
        position = held.model_copy(
            update={
                "short": held.short + executed,
                "short_cost_basis": _weighted(held.short, held.short_cost_basis, executed, price),
            }
        )
        cash += executed * price - margin
        margin_used += margin
    else:  # cover
        executed = min(quantity, held.short)
        if executed < quantity and not partial:
            raise InsufficientFunds(f"covers {quantity}, short {held.short}")
        # Cash out per share: the buy-back price less the collateral it releases.
        per_share = price - held.short_cost_basis * ratio
        if per_share > 0 and executed * per_share > cash:
            if not partial:
                needed = executed * per_share
                raise InsufficientFunds(f"cover needs {needed:,.2f}, cash is {cash:,.2f}")
            executed = int(cash // per_share)
        released = min(margin_used, executed * held.short_cost_basis * ratio)
        realized = (held.short_cost_basis - price) * executed
        remaining = held.short - executed
        position = held.model_copy(
            update={
                "short": remaining,
                "short_cost_basis": held.short_cost_basis if remaining else 0.0,
            }
        )
        cash += released - executed * price
        margin_used -= released
    if executed == 0:
        return unchanged
    positions = dict(portfolio.positions)
    if position == Position():
        positions.pop(ticker, None)
    else:
        positions[ticker] = position
    after = portfolio.model_copy(
        update={
            # Clamp only float rounding: every branch above keeps cash and margin non-negative.
            "cash": max(cash, 0.0),
            "margin_used": max(margin_used, 0.0),
            "positions": positions,
        }
    )
    return FillResult(
        portfolio=after,
        action=action,
        requested=quantity,
        executed=executed,
        price=price,
        realized_pnl=round(realized, 6),
    )
