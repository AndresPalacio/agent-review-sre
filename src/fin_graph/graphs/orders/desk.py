"""The one place an approved paper order is checked, placed and verified.

The orders graph and the chat's ``propose_order`` tool both go through :class:`OrderDesk`, so
every order meets the same rules whatever started it: a fresh limits check and fill simulation
before approval, an idempotent placement after it, and a verification against the ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from fin_graph.domain.fills import InsufficientFunds, OrderStatus, PaperExecution, apply_fill
from fin_graph.domain.orders import OrderAction, ProposedOrder, client_order_id
from fin_graph.domain.portfolio import Position
from fin_graph.integrations.broker.paper.broker import PaperBroker
from fin_graph.safety.trading_limits import check_order


class OrderPreview(BaseModel):
    """What the order would do now: the limits verdict and the portfolio before and after."""

    model_config = ConfigDict(frozen=True)

    client_order_id: str
    ticker: str
    action: OrderAction
    quantity: int
    reference_price: float
    quote: float | None
    notional: float
    allowed: bool
    violations: list[str] = Field(default_factory=list)
    max_quantity: int = 0
    cash_before: float
    cash_after: float | None = None
    position_before: Position
    position_after: Position | None = None
    nlv_before: float
    nlv_after: float | None = None

    def describe(self) -> str:
        """A one-paragraph summary in Spanish for the approver."""
        verb = {
            OrderAction.BUY: "Comprar",
            OrderAction.SELL: "Vender",
            OrderAction.SHORT: "Vender en corto",
            OrderAction.COVER: "Recomprar (cubrir corto)",
            OrderAction.HOLD: "Mantener",
        }[self.action]
        price = self.quote if self.quote is not None else self.reference_price
        text = (
            f"{verb} {self.quantity} {self.ticker} a ~{price:,.2f} "
            f"(nocional {self.notional:,.2f}). Orden paper {self.client_order_id}."
        )
        if not self.allowed:
            return f"{text} BLOQUEADA: {'; '.join(self.violations)}."
        held_before = self.position_before.net_shares
        held_after = self.position_after.net_shares if self.position_after else held_before
        return (
            f"{text} Efectivo {self.cash_before:,.2f} -> {self.cash_after or 0:,.2f}; "
            f"posición {held_before} -> {held_after} acciones; "
            f"valor neto {self.nlv_before:,.2f} -> {self.nlv_after or 0:,.2f}. "
            "Solo simulación paper: no se mueve dinero real."
        )


@dataclass(frozen=True)
class OrderDesk:
    """Checks, places and verifies paper orders through one broker."""

    broker: PaperBroker

    async def prepare(self, order: ProposedOrder) -> OrderPreview:
        """The limits verdict and the simulated fill at the current quote."""
        portfolio = await self.broker.portfolio()
        quote = await self.broker.quotes(order.ticker)
        price = quote if quote and quote > 0 else order.reference_price
        prices = {order.ticker: price}
        check = check_order(
            order,
            portfolio,
            prices,
            limits=self.broker.limits,
            orders_today=await self.broker.orders_today(),
        )
        violations = [f"{item.rule}: {item.detail}" for item in check.violations]
        drift = price / order.reference_price - 1
        if abs(drift) > self.broker.max_price_drift:
            violations.append(f"price_drift: {drift:+.1%} since the order was proposed")
        base: dict[str, Any] = {
            "client_order_id": order.client_order_id,
            "ticker": order.ticker,
            "action": order.action,
            "quantity": order.quantity,
            "reference_price": order.reference_price,
            "quote": quote,
            "notional": round(order.quantity * price, 2),
            "max_quantity": check.max_quantity,
            "cash_before": portfolio.cash,
            "position_before": portfolio.position(order.ticker),
            "nlv_before": round(portfolio.net_liquidation_value(prices), 2),
        }
        if violations:
            return OrderPreview(**base, allowed=False, violations=violations)
        try:
            fill = apply_fill(portfolio, order.ticker, order.action, order.quantity, price)
        except InsufficientFunds as exc:
            return OrderPreview(**base, allowed=False, violations=[f"insufficient_funds: {exc}"])
        after = fill.portfolio
        return OrderPreview(
            **base,
            allowed=True,
            cash_after=round(after.cash, 2),
            position_after=after.position(order.ticker),
            nlv_after=round(after.net_liquidation_value(prices), 2),
        )

    async def execute(self, order: ProposedOrder) -> PaperExecution:
        """Place ``order``; placing it again returns the first result."""
        return await self.broker.place(order)

    async def verify(self, execution: PaperExecution) -> bool:
        """The ledger holds the order as filled for its full quantity at a positive price."""
        stored = await self.broker.execution(execution.client_order_id)
        return (
            stored is not None
            and stored.status is OrderStatus.FILLED
            and stored.executed == stored.order.quantity
            and stored.fill_price is not None
            and stored.fill_price > 0
        )

    @staticmethod
    def rollback_for(execution: PaperExecution) -> ProposedOrder | None:
        """The inverse of what actually filled, at the fill price; ``None`` if nothing did."""
        order = execution.order
        if execution.executed <= 0 or execution.fill_price is None:
            return None
        inverse = order.inverse()
        return inverse.model_copy(
            update={
                "quantity": execution.executed,
                "reference_price": execution.fill_price,
                "client_order_id": client_order_id(
                    order.client_order_id,
                    order.ticker,
                    inverse.action,
                    execution.executed,
                    order.as_of,
                ),
            }
        )

    @staticmethod
    def with_quantity(order: ProposedOrder, quantity: int) -> ProposedOrder:
        """``order`` resized by an approver; a new quantity is a new order with a new id."""
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if quantity == order.quantity:
            return order
        return order.model_copy(
            update={
                "quantity": quantity,
                "client_order_id": client_order_id(
                    order.client_order_id, order.ticker, order.action, quantity, order.as_of
                ),
                "reasoning": f"{order.reasoning} (quantity edited to {quantity})".strip(),
            }
        )
