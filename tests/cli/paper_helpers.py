"""A paper broker on a temporary SQLite ledger, with a fixed quote and an open market."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fin_graph.domain.portfolio import Portfolio
from fin_graph.integrations.broker.paper.broker import PaperBroker
from fin_graph.integrations.context import RunContext
from fin_graph.persistence.paper_ledger import open_sqlite_paper_ledger
from fin_graph.safety.trading_limits import TradingLimits


async def _quote(ticker: str) -> float | None:
    return 100.0


@asynccontextmanager
async def paper_context(path: Path) -> AsyncIterator[tuple[PaperBroker, RunContext]]:
    async with open_sqlite_paper_ledger(path) as store:
        broker = PaperBroker(
            store=store,
            quotes=_quote,
            limits=TradingLimits(max_position_pct=1.0),
            initial=Portfolio(cash=10_000.0),
            clock=lambda: datetime(2024, 6, 28, 14, 0, tzinfo=UTC),
            market_open=lambda now: True,
        )
        yield broker, RunContext(clients={"paper_broker": broker})
