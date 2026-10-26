"""Open the configured paper broker for a process: its SQLite ledger, quotes and limits."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

from platformdirs import user_data_dir

from fin_graph.config.settings import Settings
from fin_graph.domain.portfolio import Portfolio
from fin_graph.integrations.analytics.engine import AnalyticsEngine
from fin_graph.integrations.broker.paper.broker import PaperBroker, QuoteSource, last_close_quotes
from fin_graph.integrations.broker.paper.tools import INTEGRATION
from fin_graph.integrations.context import RunContext
from fin_graph.integrations.market_data import MarketDataClient
from fin_graph.persistence.paper_ledger import PaperLedgerStore, open_sqlite_paper_ledger
from fin_graph.safety.trading_limits import TradingLimits


def ledger_path(settings: Settings) -> Path:
    """``PAPER_LEDGER_PATH``, or ``paper.sqlite`` in the user data folder."""
    if settings.paper_ledger_path.strip():
        return Path(settings.paper_ledger_path)
    return Path(user_data_dir("fin-graph", "fin-graph")) / "paper.sqlite"


def starting_portfolio(settings: Settings) -> Portfolio:
    """The account before its first fill: ``INITIAL_CASH`` in ``BASE_CURRENCY``."""
    return Portfolio(
        cash=settings.initial_cash,
        margin_requirement=settings.margin_requirement,
        currency=settings.base_currency,
    )


def context_quotes(context: RunContext) -> QuoteSource:
    """Last-close quotes from the market data provider the analytics engine reads."""

    async def quote(ticker: str) -> float | None:
        engine = context.client("analytics", AnalyticsEngine)
        client = context.client(engine.data_integration, object)
        if not isinstance(client, MarketDataClient):
            raise TypeError(f"{engine.data_integration} client is not a market data client")
        return await last_close_quotes(client)(ticker)

    return quote


@asynccontextmanager
async def open_trading_context(
    settings: Settings, context: RunContext, *, path: Path | None = None
) -> AsyncIterator[tuple[PaperBroker, RunContext]]:
    """The paper broker quoting from ``context``, and ``context`` with the broker added."""
    async with open_paper_broker(settings, context_quotes(context), path=path) as broker:
        yield broker, replace(context, clients={**context.clients, INTEGRATION: broker})


def paper_broker(settings: Settings, store: PaperLedgerStore, quotes: QuoteSource) -> PaperBroker:
    """The broker ``settings`` describe over ``store`` (SQLite locally, Postgres on a server)."""
    return PaperBroker(
        store=store,
        quotes=quotes,
        limits=TradingLimits.from_settings(settings),
        initial=starting_portfolio(settings),
        account_id=settings.paper_account_id,
        max_price_drift=settings.paper_max_price_drift,
        slippage_bps=settings.paper_slippage_bps,
    )


@asynccontextmanager
async def open_paper_broker(
    settings: Settings, quotes: QuoteSource, *, path: Path | None = None
) -> AsyncIterator[PaperBroker]:
    """A broker over the configured SQLite ledger; the ledger closes when the block ends."""
    async with open_sqlite_paper_ledger(path or ledger_path(settings)) as store:
        yield paper_broker(settings, store, quotes)
