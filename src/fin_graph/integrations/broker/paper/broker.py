"""The paper broker: fills approved orders against a simulated portfolio, never a real account.

A market order fills at the latest quote (the last close the data provider has), moved against
the trader by ``slippage_bps``. Before it fills, three checks run on the state at that moment:

- **Market hours.** Outside the NYSE regular session the order is ``queued``; :meth:`settle`
  fills it at the next open and :meth:`cancel` may withdraw it until then.
- **Price drift.** If the quote moved more than ``max_price_drift`` from the price the human
  approved, the order is rejected: the approval was for a different trade.
- **Hard limits.** :func:`~fin_graph.safety.trading_limits.check_order` runs again with the
  current portfolio and the day's fills (kill switch, notional, position size, shorting).

Every order is keyed by its ``client_order_id``, so placing it again returns the first result.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from fin_graph.domain.fills import (
    InsufficientFunds,
    OrderStatus,
    PaperExecution,
    apply_fill,
)
from fin_graph.domain.market_hours import is_market_open, next_open, session_date
from fin_graph.domain.orders import OrderAction, ProposedOrder
from fin_graph.domain.portfolio import Portfolio
from fin_graph.integrations.market_data import MarketDataClient
from fin_graph.persistence.paper_ledger import PaperLedgerStore
from fin_graph.safety.trading_limits import TradingLimits, check_order

QuoteSource = Callable[[str], Awaitable[float | None]]
#: Buying pays more and selling receives less: slippage always costs the trader.
_PAYS_MORE = frozenset({OrderAction.BUY, OrderAction.COVER})
_CAS_ATTEMPTS = 5


def last_close_quotes(
    client: MarketDataClient, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
) -> QuoteSource:
    """Quotes from the provider's latest daily close up to today (a week back covers holidays)."""

    async def quote(ticker: str) -> float | None:
        today = clock().date()
        prices = await client.prices(ticker, today - timedelta(days=7), today)
        closes = [item.close for item in sorted(prices, key=lambda item: item.time)]
        return float(closes[-1]) if closes and closes[-1] > 0 else None

    return quote


class PaperBrokerError(ValueError):
    """A request the broker refuses outright (unknown id, reused id, filled order)."""


@dataclass
class PaperBroker:
    """Approved orders → fills in ``store``; one portfolio per ``account_id``."""

    store: PaperLedgerStore
    quotes: QuoteSource
    limits: TradingLimits
    initial: Portfolio = field(default_factory=Portfolio)
    account_id: str = "paper"
    max_price_drift: float = 0.05
    slippage_bps: float = 0.0
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    market_open: Callable[[datetime], bool] = is_market_open

    def __post_init__(self) -> None:
        if not 0 < self.max_price_drift <= 1 or not 0 <= self.slippage_bps <= 500:
            raise ValueError("price drift must be in (0, 1] and slippage in [0, 500] bps")

    async def portfolio(self) -> Portfolio:
        """The account's portfolio; the configured starting one before the first fill."""
        stored, _ = await self.store.account(self.account_id)
        return self.initial if stored is None else Portfolio.model_validate(stored)

    async def orders_today(self) -> int:
        """Orders filled or waiting in today's session; the daily limit counts both."""
        return await self.store.count(
            self.account_id,
            session_date=self._session().isoformat(),
            statuses=(OrderStatus.FILLED.value, OrderStatus.QUEUED.value),
        )

    async def execution(self, client_order_id: str) -> PaperExecution | None:
        stored = await self.store.order(client_order_id)
        return None if stored is None else PaperExecution.model_validate(stored)

    async def orders(self, *, status: OrderStatus | None = None) -> list[PaperExecution]:
        rows = await self.store.orders(self.account_id, statuses=[status.value] if status else [])
        return [PaperExecution.model_validate(row) for row in rows]

    async def place(self, order: ProposedOrder) -> PaperExecution:
        """Fill ``order`` now, or queue it until the market opens; idempotent by its id."""
        if order.action is OrderAction.HOLD:
            raise PaperBrokerError("hold is not an order")
        now = self.clock()
        queued = PaperExecution(
            order=order,
            status=OrderStatus.QUEUED,
            reason=f"market closed; fills at {next_open(now).isoformat()}",
            submitted_at=now,
            updated_at=now,
        )
        created, stored = await self.store.submit(
            self.account_id,
            order.client_order_id,
            ticker=order.ticker,
            session_date=self._session(now).isoformat(),
            execution=queued.model_dump(mode="json"),
            at=now,
        )
        execution = PaperExecution.model_validate(stored)
        if not created:
            if execution.order != order:
                raise PaperBrokerError("client_order_id already used for a different order")
            return execution
        if not self.market_open(now):
            return execution
        return await self._fill(execution)

    async def cancel(self, client_order_id: str) -> PaperExecution:
        """Withdraw a queued order. A filled order cannot be cancelled, only reversed."""
        execution = await self.execution(client_order_id)
        if execution is None:
            raise PaperBrokerError(f"no paper order {client_order_id}")
        if execution.status is OrderStatus.FILLED:
            raise PaperBrokerError(
                f"{client_order_id} already filled at {execution.fill_price}; "
                "propose the inverse order to undo it"
            )
        if execution.done:
            return execution
        now = self.clock()
        cancelled = execution.model_copy(
            update={"status": OrderStatus.CANCELLED, "reason": "cancelled", "updated_at": now}
        )
        outcome = await self.store.finish(
            self.account_id,
            client_order_id,
            cancelled.model_dump(mode="json"),
            status=cancelled.status.value,
            at=now,
        )
        return cancelled if outcome == "ok" else await self._current(client_order_id)

    async def settle(self) -> list[PaperExecution]:
        """Fill the queued orders, oldest first, when the market is open; else nothing."""
        if not self.market_open(self.clock()):
            return []
        return [await self._fill(item) for item in await self.orders(status=OrderStatus.QUEUED)]

    async def _fill(self, execution: PaperExecution) -> PaperExecution:
        order = execution.order
        quote = await self.quotes(order.ticker)
        if quote is None or quote <= 0:
            return await self._reject(execution, "no quote for the ticker")
        drift = quote / order.reference_price - 1
        if abs(drift) > self.max_price_drift:
            return await self._reject(
                execution,
                f"price moved {drift:+.1%} since approval "
                f"({order.reference_price:,.2f} -> {quote:,.2f}); propose it again",
            )
        sign = 1 if order.action in _PAYS_MORE else -1
        price = round(quote * (1 + sign * self.slippage_bps / 10_000), 4)
        for _ in range(_CAS_ATTEMPTS):
            stored, version = await self.store.account(self.account_id)
            portfolio = self.initial if stored is None else Portfolio.model_validate(stored)
            filled_today = await self.store.count(
                self.account_id,
                session_date=self._session().isoformat(),
                statuses=(OrderStatus.FILLED.value,),
            )
            check = check_order(
                order,
                portfolio,
                {order.ticker: price},
                limits=self.limits,
                orders_today=filled_today,
            )
            if not check.allowed:
                detail = "; ".join(f"{item.rule}: {item.detail}" for item in check.violations)
                return await self._reject(execution, f"limits at fill time: {detail}")
            try:
                fill = apply_fill(portfolio, order.ticker, order.action, order.quantity, price)
            except InsufficientFunds as exc:
                return await self._reject(execution, str(exc))
            now = self.clock()
            done = execution.model_copy(
                update={
                    "status": OrderStatus.FILLED,
                    "executed": fill.executed,
                    "fill_price": price,
                    "realized_pnl": fill.realized_pnl,
                    "reason": f"filled at {price:,.4f}",
                    "updated_at": now,
                }
            )
            outcome = await self.store.finish(
                self.account_id,
                order.client_order_id,
                done.model_dump(mode="json"),
                status=done.status.value,
                at=now,
                portfolio=fill.portfolio.model_dump(mode="json"),
                expected_version=version,
            )
            if outcome == "ok":
                return done
            if outcome == "not_queued":
                return await self._current(order.client_order_id)
        raise RuntimeError("the paper portfolio kept changing; try the order again")

    async def _reject(self, execution: PaperExecution, reason: str) -> PaperExecution:
        now = self.clock()
        rejected = execution.model_copy(
            update={"status": OrderStatus.REJECTED, "reason": reason, "updated_at": now}
        )
        outcome = await self.store.finish(
            self.account_id,
            execution.client_order_id,
            rejected.model_dump(mode="json"),
            status=rejected.status.value,
            at=now,
        )
        return rejected if outcome == "ok" else await self._current(execution.client_order_id)

    async def _current(self, client_order_id: str) -> PaperExecution:
        current = await self.execution(client_order_id)
        if current is None:
            raise PaperBrokerError(f"no paper order {client_order_id}")
        return current

    def _session(self, now: datetime | None = None) -> date:
        return session_date(now or self.clock())
