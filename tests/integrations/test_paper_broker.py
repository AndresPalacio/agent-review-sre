from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from fin_graph.domain.fills import OrderStatus
from fin_graph.domain.market import Price
from fin_graph.domain.orders import OrderAction, ProposedOrder
from fin_graph.domain.portfolio import Portfolio
from fin_graph.domain.tools import SideEffectLevel, read_tool_meta
from fin_graph.integrations.broker.paper.broker import (
    PaperBroker,
    PaperBrokerError,
    last_close_quotes,
)
from fin_graph.integrations.broker.paper.tools import cancel_paper_order, list_paper_orders
from fin_graph.integrations.context import RunContext
from fin_graph.integrations.market_data import StaticMarketData
from fin_graph.integrations.tooling import invoke_tool
from fin_graph.persistence.paper_ledger import PaperLedgerStore, open_sqlite_paper_ledger
from fin_graph.safety.trading_limits import TradingLimits

NOW = datetime(2024, 6, 28, 14, 0, tzinfo=UTC)  # Friday 10:00 in New York


def order(
    action: OrderAction = OrderAction.BUY, quantity: int = 10, price: float = 100.0, key: str = "1"
) -> ProposedOrder:
    return ProposedOrder(
        client_order_id=f"ord_{key}",
        ticker="AAPL",
        action=action,
        quantity=quantity,
        reference_price=price,
        as_of=date(2024, 6, 28),
    )


class Quotes:
    def __init__(self, price: float | None = 100.0) -> None:
        self.price = price

    async def __call__(self, ticker: str) -> float | None:
        return self.price


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[PaperLedgerStore]:
    async with open_sqlite_paper_ledger(tmp_path / "paper.sqlite") as ledger:
        yield ledger


def broker(
    store: PaperLedgerStore,
    *,
    quotes: Quotes | None = None,
    open_market: bool = True,
    limits: TradingLimits | None = None,
    cash: float = 10_000.0,
    slippage_bps: float = 0.0,
) -> PaperBroker:
    return PaperBroker(
        store=store,
        quotes=quotes or Quotes(),
        limits=limits or TradingLimits(max_order_notional=50_000, max_position_pct=1.0),
        initial=Portfolio(cash=cash),
        slippage_bps=slippage_bps,
        clock=lambda: NOW,
        market_open=lambda now: open_market,
    )


async def test_a_market_order_fills_at_the_quote_with_slippage_and_once(
    store: PaperLedgerStore,
) -> None:
    paper = broker(store, slippage_bps=5)

    filled = await paper.place(order())
    again = await paper.place(order())

    assert filled.status is OrderStatus.FILLED and filled.fill_price == 100.05
    assert again == filled
    portfolio = await paper.portfolio()
    assert portfolio.position("AAPL").long == 10
    assert portfolio.cash == pytest.approx(10_000 - 1_000.5)
    with pytest.raises(PaperBrokerError, match="different order"):
        await paper.place(order(quantity=11))
    assert await paper.orders_today() == 1


async def test_outside_market_hours_orders_queue_and_can_be_cancelled(
    store: PaperLedgerStore,
) -> None:
    closed = broker(store, open_market=False)

    queued = await closed.place(order())
    cancelled = await closed.cancel(queued.client_order_id)

    assert queued.status is OrderStatus.QUEUED and "fills at" in queued.reason
    assert cancelled.status is OrderStatus.CANCELLED
    assert (await closed.portfolio()).positions == {}
    second = await closed.place(order(key="2"))
    opened = broker(store)
    [settled] = await opened.settle()
    assert settled.client_order_id == second.client_order_id
    assert settled.status is OrderStatus.FILLED
    with pytest.raises(PaperBrokerError, match="inverse"):
        await opened.cancel(second.client_order_id)


async def test_fill_time_checks_reject_drift_limits_and_missing_cash(
    store: PaperLedgerStore,
) -> None:
    drifted = await broker(store, quotes=Quotes(110.0)).place(order(key="d"))
    halted = await broker(store, limits=TradingLimits(kill_switch=True)).place(order(key="k"))
    poor = await broker(store, cash=500.0).place(order(key="c"))
    unquoted = await broker(store, quotes=Quotes(None)).place(order(key="q"))

    assert drifted.status is OrderStatus.REJECTED and "+10.0%" in drifted.reason
    assert halted.status is OrderStatus.REJECTED and "kill_switch" in halted.reason
    assert poor.status is OrderStatus.REJECTED and "insufficient_cash" in poor.reason
    assert unquoted.status is OrderStatus.REJECTED and "no quote" in unquoted.reason


async def test_concurrent_fills_never_spend_the_same_cash_twice(
    store: PaperLedgerStore,
) -> None:
    paper = broker(store, cash=10_000.0)

    results = await asyncio.gather(
        paper.place(order(quantity=60, key="a")), paper.place(order(quantity=60, key="b"))
    )

    statuses = sorted(item.status.value for item in results)
    assert statuses == ["filled", "rejected"]
    portfolio = await paper.portfolio()
    assert portfolio.position("AAPL").long == 60 and portfolio.cash == 4_000.0


async def test_quotes_come_from_the_last_close(tmp_path: Path) -> None:
    bars = [
        Price(time=f"2024-06-{day}T20:00:00Z", open=1, high=1, low=1, close=close, volume=1)
        for day, close in (("26", 98.0), ("27", 99.5))
    ]
    market = StaticMarketData(prices={"AAPL": bars})

    quote = last_close_quotes(market, clock=lambda: NOW)

    assert await quote("AAPL") == 99.5
    assert await quote("MSFT") is None


async def test_agent_tools_read_orders_and_cancel_only_queued_ones(
    store: PaperLedgerStore,
) -> None:
    paper = broker(store, open_market=False)
    queued = await paper.place(order())
    context = RunContext(clients={"paper_broker": paper})

    listed = await invoke_tool(list_paper_orders, {}, context, call_id="list")
    cancelled = await invoke_tool(
        cancel_paper_order, {"client_order_id": queued.client_order_id}, context, call_id="cancel"
    )
    refused = await invoke_tool(
        cancel_paper_order, {"client_order_id": "ord_missing"}, context, call_id="missing"
    )

    assert queued.client_order_id in str(listed.content)
    assert '"status":"cancelled"' in str(cancelled.content).replace(" ", "")
    assert refused.status == "error" and "no paper order" in str(refused.content)
    meta = read_tool_meta(cancel_paper_order)
    assert meta is not None and meta.side_effect_level is SideEffectLevel.MUTATING
