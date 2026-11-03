from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from fin_graph.domain.fills import OrderStatus
from fin_graph.domain.orders import OrderAction, ProposedOrder
from fin_graph.domain.portfolio import Portfolio
from fin_graph.integrations.broker.paper.broker import PaperBroker
from fin_graph.persistence.paper_ledger import PaperLedgerStore, open_sqlite_paper_ledger
from fin_graph.quant.execution import V2PaperBroker
from fin_graph.quant.portfolio import combine_signals, size_from_signal
from fin_graph.quant.signals.base import Signal
from fin_graph.safety.trading_limits import TradingLimits


def signal(value: float, model: str = "a") -> Signal:
    return Signal(
        model_name=model,
        ticker="CASE",
        as_of=date(2025, 1, 2),
        value=value,
        reasoning=model,
    )


def test_consensus_weighting_validation_and_sizing() -> None:
    combined = combine_signals([signal(1, "a"), signal(-0.5, "b")], {"a": 3, "b": 1})
    assert combined.value == pytest.approx(0.625)
    assert combined.metadata["bullish_count"] == 1
    assert combined.metadata["bearish_count"] == 1

    with pytest.raises(ValueError, match="non-negative"):
        combine_signals([signal(1)], {"a": -1})
    with pytest.raises(ValueError, match="positive"):
        combine_signals([signal(1)], {"a": 0})

    order = size_from_signal(
        signal(0.8),
        Portfolio(cash=10_000),
        100,
        TradingLimits(max_order_notional=5_000, max_position_pct=0.2),
    )
    assert order is not None
    assert order.action is OrderAction.BUY
    assert order.quantity == 20


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[PaperLedgerStore]:
    async with open_sqlite_paper_ledger(tmp_path / "v2-paper.sqlite") as ledger:
        yield ledger


async def test_v2_broker_adapter_preserves_native_idempotent_buy_sell_short_cover(
    store: PaperLedgerStore,
) -> None:
    now = datetime(2025, 1, 2, 15, tzinfo=UTC)

    async def quote(ticker: str) -> float:
        return 100

    native = PaperBroker(
        store=store,
        quotes=quote,
        limits=TradingLimits(
            max_order_notional=100_000,
            max_position_pct=1,
            short_selling_enabled=True,
        ),
        initial=Portfolio(cash=10_000),
        clock=lambda: now,
        market_open=lambda _: True,
    )
    adapter = V2PaperBroker(native)

    def order(action: OrderAction, quantity: int, key: str) -> ProposedOrder:
        return ProposedOrder(
            client_order_id=f"v2-{key}",
            ticker="CASE",
            action=action,
            quantity=quantity,
            reference_price=100,
            as_of=now.date(),
        )

    buy = await adapter.place(order(OrderAction.BUY, 10, "buy"))
    duplicate = await adapter.place(order(OrderAction.BUY, 10, "buy"))
    sell = await adapter.place(order(OrderAction.SELL, 10, "sell"))
    short = await adapter.place(order(OrderAction.SHORT, 5, "short"))
    cover = await adapter.place(order(OrderAction.COVER, 5, "cover"))
    snapshot = await adapter.snapshot()

    assert all(item.status is OrderStatus.FILLED for item in (buy, sell, short, cover))
    assert duplicate == buy
    assert snapshot["cash"] == pytest.approx(10_000)
    assert snapshot["positions"] == {}
    assert len(snapshot["trade_history"]) == 4
