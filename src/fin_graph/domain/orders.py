"""Paper orders: what the portfolio may do per ticker, the decision, and the order it becomes.

``compute_allowed_actions`` is ported from ai-hedge-fund ``src/agents/portfolio_manager.py``
(MIT). It is the deterministic gate. The LLM picks an action and a quantity only inside it,
:func:`clamp_decision` enforces that, and anything else becomes ``hold``. Orders are paper only.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from fin_graph.domain.portfolio import Portfolio


class OrderAction(StrEnum):
    """Buy and sell trade the long side; short and cover the short side."""

    BUY = "buy"
    SELL = "sell"
    SHORT = "short"
    COVER = "cover"
    HOLD = "hold"


#: The order that undoes each action (a rollback proposes it, never runs it on its own).
INVERSE = {
    OrderAction.BUY: OrderAction.SELL,
    OrderAction.SELL: OrderAction.BUY,
    OrderAction.SHORT: OrderAction.COVER,
    OrderAction.COVER: OrderAction.SHORT,
    OrderAction.HOLD: OrderAction.HOLD,
}


def compute_allowed_actions(
    tickers: Sequence[str],
    prices: Mapping[str, float],
    max_shares: Mapping[str, int],
    portfolio: Portfolio,
    *,
    allow_short: bool = True,
) -> dict[str, dict[OrderAction, int]]:
    """Each ticker's possible actions and their maximum quantity; ``hold`` is always there.

    Buy is capped by the risk limit (``max_shares``) and by cash; sell and cover by the shares
    held; short by the risk limit and by margin. Margin uses the net liquidation value as
    equity, where the original fell back to cash.
    """
    equity = portfolio.equity(prices)
    allowed: dict[str, dict[OrderAction, int]] = {}
    for ticker in tickers:
        price = float(prices.get(ticker, 0.0))
        held = portfolio.position(ticker)
        limit = int(max_shares.get(ticker, 0) or 0)
        actions: dict[OrderAction, int] = {OrderAction.HOLD: 0}
        if price > 0 and portfolio.cash > 0:
            buy = max(0, min(limit, int(portfolio.cash // price)))
            if buy:
                actions[OrderAction.BUY] = buy
        if held.long > 0:
            actions[OrderAction.SELL] = held.long
        if allow_short and price > 0 and limit > 0:
            if portfolio.margin_requirement <= 0:
                short = limit
            else:
                available = max(0.0, equity / portfolio.margin_requirement - portfolio.margin_used)
                short = max(0, min(limit, int(available // price)))
            if short:
                actions[OrderAction.SHORT] = short
        if held.short > 0:
            actions[OrderAction.COVER] = held.short
        allowed[ticker] = actions
    return allowed


class PortfolioDecision(BaseModel):
    """One ticker's decision: an allowed action, a quantity within its maximum, and why."""

    action: OrderAction = Field(description="One of the allowed actions for this ticker.")
    quantity: int = Field(ge=0, description="Shares; at most the allowed maximum, 0 for hold.")
    confidence: float = Field(default=0, ge=0, le=100, description="0-100.")
    reasoning: str = Field(default="", description="Why, in one short sentence.")


def hold(reason: str, confidence: float = 0) -> PortfolioDecision:
    """The decision that changes nothing."""
    return PortfolioDecision(
        action=OrderAction.HOLD, quantity=0, confidence=confidence, reasoning=reason
    )


def clamp_decision(
    decision: PortfolioDecision, allowed: Mapping[OrderAction, int]
) -> tuple[PortfolioDecision, str | None]:
    """Keep ``decision`` inside ``allowed``; returns the safe decision and what was changed.

    An action that is not allowed becomes ``hold``. A quantity above the maximum is cut to it,
    and a non-hold action with quantity 0 also becomes ``hold``.
    """
    if decision.action is OrderAction.HOLD:
        return decision.model_copy(update={"quantity": 0}), None
    maximum = allowed.get(decision.action)
    if maximum is None:
        return hold(f"{decision.action.value} is not allowed: {decision.reasoning}"), (
            f"{decision.action.value} not allowed"
        )
    if decision.quantity <= 0:
        return hold(f"no quantity given: {decision.reasoning}"), "quantity 0"
    if decision.quantity > maximum:
        return decision.model_copy(update={"quantity": maximum}), (
            f"quantity {decision.quantity} cut to the maximum {maximum}"
        )
    return decision, None


class ProposedOrder(BaseModel):
    """A paper order awaiting approval. ``client_order_id`` makes execution idempotent."""

    model_config = ConfigDict(frozen=True)

    client_order_id: str
    ticker: str
    action: OrderAction
    quantity: int = Field(gt=0)
    reference_price: float = Field(gt=0)
    as_of: date
    confidence: float = Field(default=0, ge=0, le=100)
    reasoning: str = ""
    thesis_category: str = ""

    @property
    def notional(self) -> float:
        """Quantity times the reference price."""
        return self.quantity * self.reference_price

    @property
    def side(self) -> str:
        """``long`` for buy/sell, ``short`` for short/cover."""
        return "long" if self.action in (OrderAction.BUY, OrderAction.SELL) else "short"

    def inverse(self) -> ProposedOrder:
        """The order that undoes this one, for a rollback proposal."""
        return self.model_copy(
            update={
                "client_order_id": f"{self.client_order_id}-rollback",
                "action": INVERSE[self.action],
                "reasoning": f"rollback of {self.client_order_id}",
            }
        )


def client_order_id(
    run_id: str, ticker: str, action: OrderAction, quantity: int, as_of: date
) -> str:
    """A stable id: proposing the same order in the same run twice yields the same id."""
    canonical = json.dumps(
        [run_id, ticker, action.value, quantity, as_of.isoformat()], separators=(",", ":")
    )
    return "ord_" + hashlib.sha256(canonical.encode()).hexdigest()[:16]


def propose_order(
    decision: PortfolioDecision,
    *,
    ticker: str,
    price: float,
    as_of: date,
    run_id: str,
    thesis_category: str = "",
) -> ProposedOrder | None:
    """The order for a non-hold decision with a positive price, else ``None``."""
    if decision.action is OrderAction.HOLD or decision.quantity <= 0 or price <= 0:
        return None
    return ProposedOrder(
        client_order_id=client_order_id(run_id, ticker, decision.action, decision.quantity, as_of),
        ticker=ticker,
        action=decision.action,
        quantity=decision.quantity,
        reference_price=price,
        as_of=as_of,
        confidence=decision.confidence,
        reasoning=decision.reasoning,
        thesis_category=thesis_category,
    )
